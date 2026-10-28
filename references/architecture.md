# NeuroMem 架构设计说明

本文档说明 `neuromem.py` 的分层记忆模型、数据布局、检索打分公式与三级缓存机制，供引擎调优与二次开发参考。

## 1. 分层记忆模型（人脑仿生）

```
输入信息
   │
   ▼
┌─ 瞬时工作记忆（working）────────────────────────────┐
│  对应人脑工作记忆，会话粒度，高速读写               │
└───────────┬────────────────────────────────────────┘
            │ 会话结束自动筛选、降权
            ▼
┌─ 短时记忆（short）─────────────────────────────────┐
│  7-30 天窗口，高频访问，自动去重                   │
│  定期巩固 consolidate 迁移+画像提炼                │
└───────────┬────────────────────────────────────────┘
            │ 重要性≥0.6 / 访问≥3次 / 存在≥7天 → 升级
            ▼
┌─ 长时永久记忆（long）──────────────────────────────┐
│  episodic 情景（含实体/场景上下文）                │
│  semantic 语义（事实/偏好/习惯）                   │
│  procedural 程序（流程/技能/模板，自动识别）       │
└────────────────────────────────────────────────────┘
```

状态机：`active（生效）→ pending（待验证，发现矛盾时）→ invalid（已修正/多次否定，移入 archive）`；`forget` 直接 `active → archived`。**任何状态变化都先写版本快照，物理文件只移到 archive，永不删除**（"永不丢失原则"）。

## 2. 数据目录布局

```
<root>/users/<user_id>/
├── config.json              # 画像 profile、遗忘策略、encrypted 开关
├── memories/<tier>/<subtype>/<mem_id>.json   # 记忆条目
├── index/inverted.json      # 倒排索引 {token: [mem_id...]}
├── index/graph.json         # 关联图谱 {mem_id: {neighbor: strength}}
├── versions/<mem_id>.json   # 版本历史快照数组（写时复制）
├── archive/                 # 失效/归档记忆（物理保留）
├── audit.jsonl              # 操作审计（append-only）
├── cache-hot.jsonl          # 一级热缓存（会话内，<1ms）
├── cache-warm.jsonl         # 二级温缓存（本地高速，30 天）
└── cache-cold.jsonl         # 三级冷缓存（永久）
```

记忆条目 JSON 字段：

| 字段 | 说明 |
|------|------|
| `id` | 唯一 ID（`m_<毫秒时间戳>_<md5>`） |
| `text` / `summary` | 原文 / 提炼摘要（可加密） |
| `tier` / `subtype` | 层级 / 子类型 |
| `tags` / `entities` | 标签 / 实体（`{"person":[...], "place":[...]}`） |
| `importance` / `strength` | 重要性 / 记忆强度（访问强化，模拟反复提取加深） |
| `weight` | 遗忘权重（demote 降低，全局缩放检索分） |
| `status` | active / pending / invalid / archived |
| `access_count` / `last_access` | 使用频次统计 |
| `session_id` / `source` / `context` | 会话 / 来源 / 情境 |
| `version` / `superseded_by` / `links` | 版本号 / 被谁取代（修正链）/ 关联记忆 |

## 3. 检索打分公式

```
score = 0.55 × 语义相关 + 0.20 × 重要性 + 0.10 × 使用频次 + 0.10 × 时间新近 + 0.05 × 记忆强度 + 情境加成
最终值 × 遗忘权重 weight
```

- **语义相关**：中英文混合 token 化（英文单词 + 中文 bigram + 数字/实体），Jaccard 相似度加短文本包含度修正；同时取 summary、tags、entities 三路最大值。
- **时间新近**：半衰期衰减 `1/(1+age/half_life)`，短时记忆半衰 14 天、长时 60 天。
- **情境加成**：查询上下文与记忆 `context` 字段键值匹配时加 `匹配度×0.12`。
- **联想扩展**：命中 Top-K 后沿图谱以 `分数×边强度×0.6` 追加关联记忆（最多 `max(3, top_k/2)` 条）。
- **碎片补全**：短摘要记忆（<24 字）命中后，先取最强图邻居，无图边时按共享标签/实体找同主题记忆，推测补全并显式标注 `_completion.inferred: true`（元记忆：不编造、标注推测）。

## 4. 去重与关联建边

- 新增记忆与同层 active/pending 记忆比对：相似度 ≥0.85 → **合并**（保留访问更多/更长文本者，累积访问与标签）；≥0.42 → **建图谱边**（索引增量更新）。
- 图谱重建：记忆量 ≤500 时全量两两建边，更大时靠增量边（`update_index_add`），控制写入成本。

## 5. 矛盾检测规则

三类规则化检测（无需 LLM）：

1. **数值断言冲突**：正则抽取 `数字+单位`，同单位不同值且文本相似度 ≥0.25 → 冲突（`kind: numeric`）。
2. **时间线冲突**（需求 3.3）：抽取显式时间点（YYYY年M月 / YYYY-MM-DD / YYYY年 / M月 / 第Q季度 / HH:MM，`_TIME_RE`），同维度（均为年/月/季度/时刻）不同值、且至少一句含时间动词（上线/发布/开始/截止/推迟等）→ 冲突（`kind: timeline`）。
3. **情感/断言极性冲突**：预置词对（喜欢/不喜欢，是/不是，支持/反对，在/不在）正负向互现 → 冲突（`kind: polarity`）。

处理链路：新写入 `--auto-conflict` 命中 → 旧记忆标 `pending` 留痕；`consolidate --auto-resolve` 时被 ≥2 次否定的 `pending` 自动标 `invalid` 归档（渐进式修正，不暴力覆盖）；`correct` 修正时按旧记忆标签自动联动失效相关缓存条目（需求 3.3 缓存同步失效）。

## 6. 三级语义缓存与成本模型

| 层级 | 对应记忆 | 存储 | 命中阈值 | 说明 |
|------|----------|------|----------|------|
| hot | 工作记忆 | 内存级（jsonl 首行） | 0.90 | 会话内高频，<1ms |
| warm | 短时记忆 | 本地 | 0.85 | 30 天窗口 |
| cold | 长时记忆 | 本地+云端 | 0.80 | 永久 |

- **命中免单**：`cache query` 命中直接返回 `answer`，零大模型调用。
- **前置拦截**：`search`（CLI 与 HTTP）默认先打缓存，命中直接返回 `from_cache: true`，未命中才走全量记忆检索（需求 3.2/8.1.2 enable_cache，`--no-cache`/`enable_cache=false` 关闭）。
- **自动升温**：冷/温命中后复制写入更热一级（`write-through`），长期未命中热缓存自然被新条目挤出。
- **防脏缓存**：`correct` 修正记忆时按旧记忆标签自动 `cache invalidate`；也可手动 `cache invalidate --query 关键词` 按关键词联动清除对应条目。
- **同问同答去重**：`cache store` 时若已有相似度 ≥0.95 且答案相同的条目，只刷新时间不新增。
- **成本链路**：API 侧约定「先 cache → 未命中再 search 压缩上下文（召回核心片段替代全量历史）→ 最后才调大模型 → 结果双写入（记忆+缓存）」，即需求文档 3.8 节 10%-30% 输入压缩与结果双写入的落地实现。

## 7. 同步与并发约定

- 增量同步按 `(version, updated_at)` 元组比较，新者胜；两端独立修改保留新版本，archive 记忆不参与。
- 多用户隔离：一切路径挂在 `users/<user_id>/` 下，`--user` 是唯一隔离维度；`serve` 单用户绑定。
- 审计：`add/correct/forget/consolidate/cache/sync/import` 全部写 `audit.jsonl`，可回溯调用链。

## 8. 可调参数速查

| 位置 | 参数 | 默认 | 说明 |
|------|------|------|------|
| `TIERS` / `SUBTYPES` / `STATUSES` | 常量 | - | 扩展层级/子类型/状态 |
| `STOPWORDS` | 停用词表 | 中文基础词 | 检索质量调优 |
| `_score` 权重 | 0.55/0.20/0.10/0.10/0.05 | - | 检索排序调优 |
| `CACHE_SIM_THRESHOLD` | 0.90/0.85/0.80 | - | 缓存命中敏感度 |
| 合并/建边阈值 | 0.85 / 0.42 | - | 去重激进程度 |

## 9. LLM 增强位（可选，缺省零依赖）

`add --llm-summarize` 走 OpenAI 兼容 `chat/completions`（urllib 直连 + `ProxyHandler({})` 绕过失效代理），用于把长文压成 ≤50 字长期记忆摘要。未配置 Key 时静默降级为原文摘要，全链路不依赖外部服务。

---

# v2.0 架构增补

## 检索打分（v2.0 融合版）

```
sem_literal = jaccard(char_bigram(query), char_bigram(summary))  # 字面相近（中文 bigram）
sem         = max(sem_literal, tag_sim*0.8, entity_sim*0.7)      # 摘要/标签/实体三处取最优
vec         = cos(hashing_tf_vector(query), hashing_tf_vector(summary+tags))  # 512 维哈希向量
bm25        = Σ idf(t) * tf_sat(t) * length_norm                 # 词频饱和 + 长度归一化
bm_norm     = min(1.0, bm25 / 8.0)                               # 归一化经验上界
score = (0.45*sem + 0.15*vec + 0.10*bm_norm + 0.15*imp
         + 0.05*freq + 0.05*recency + 0.05*strength + ctx_bonus) * forget_weight
         # ctx_bonus 上限 +0.12；forget_weight 来自 demote/遗忘曲线
```

- **三路并存的理由**：字面相似管"原话复述"，哈希向量管"换个说法也能撞上"，BM25 管"关键词精确命中 + 长文档不算亏"，三者量纲统一到 0~1 后加权求和，避免单路失效就整体崩塌。
- **BM25 的作用**：长文档（如会议纪要）不被短句压分，同时高频词不会无限加权（`tf` 饱和）。
- **远程 Embedding 可选**：配 `NEUROMEM_EMBED_*` 时，`sem_vector` 换成远程向量；请求失败静默回退哈希向量，检索不中断。

## 反馈闭环与自我进化

```
correct(旧→新)
   ├─► 旧记忆 status=invalid + 版本快照（可回溯）
   ├─► 新记忆 links=[旧ID]（纠偏链）
   ├─► cache_invalidate(旧摘要关键词)（防脏缓存）
   └─► config.correction_rules += {topic, old, new, ts}
                │
                ▼ evolve
        按 topic 聚类，count≥2 → 生成 procedural 记忆（"关于X，历史纠正N次，以最近一次为准"）
                │
                ▼
        参与后续检索排序（规则记忆本身可被召回，作为回答约束）
```

## 图谱结构

`index/graph.json`：`{"nodes": {entity: {type, count, mems: [id...]}}, "edges": {"A|B": {weight, mems: [...]}}}`。
实体抽取零依赖（人名/组织/项目/数字/英文专名规则），`add` / `capture` / `correct` / `evolve` 增量维护；`--entity` 查询走邻接表 O(deg)。

## 加密格式

```
enc:v2:<base64( hmac_sha256(32) | salt(16) | nonce(8) | ciphertext )>
                 └─ 覆盖 salt+nonce+ciphertext ┘  └─ scrypt 派生密钥（n=2^14, r=8, p=1）
```

解密顺序：先验 MAC 再解密（encrypt-then-MAC），密钥错误与密文篡改都会在同一步失败，返回统一 `integrity check failed` 错误，不泄露区分信息。`enc:v1` 仅做密钥流异或、无认证，仍可读但建议逐步迁移。

## 后台巩固调度

守护循环每轮：读 `audit.jsonl` 尾部 → 若最近 `--busy-window` 分钟内出现 `add/correct/capture` 则判定忙、睡满一个 interval 后重试 → 否则执行一次静默巩固（短时→长时迁移、去重、画像提炼）→ 记录审计。忙退避避免与在线写入竞争文件锁。
