# NeuroMem API 参考手册

引擎入口：`python neuromem.py <command> [options]`。所有命令支持 `--user <id>`（默认 `default`）实现多租户隔离，输出统一为 JSON，成功 `"ok": true`，失败 `"ok": false` 且带 `"error"`。

全局参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--dir` | `~/.neuromem`（或 `NEUROMEM_DIR`） | 记忆数据根目录 |
| `--user` | `default` | 用户 ID，数据物理隔离 |

---

## 1. add — 写入记忆

类脑写入链路：**事实提取 → 分层编码 → 去重合并 → 索引更新 → 图谱建边 → 矛盾检测 → 审计**。

```
python neuromem.py add --user <id> --text "内容" [options]
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--text` | string | 必填 | 记忆正文（`--file` 与 `--text` 二选一） |
| `--file` | path | - | 从文件读取正文 |
| `--tier` | working/short/long | short | 强制分层（不传则自动分级） |
| `--subtype` | general/episodic/semantic/procedural | 自动推断 | 子类型；`procedural` 关键词自动识别工作流程类 |
| `--tags` | list | - | 标签，如 `--tags 偏好 咖啡` |
| `--entities` | JSON | `{}` | 实体，如 `{"person":["张三"],"place":["杭州"]}` |
| `--importance` | float 0-1 | 0.4 | 重要性（≥0.6 倾向升长时） |
| `--session` | string | - | 会话 ID（工作记忆维度） |
| `--source` | string | - | 来源应用/设备 |
| `--context-json` | JSON | `{}` | 情境，如 `{"scene":"出差","device":"pc"}`，检索时同情境加权 |
| `--llm-summarize` | flag | off | 用 LLM 提炼摘要（需配 `NEUROMEM_LLM_*`） |
| `--auto-conflict` | flag | off | 与已有记忆矛盾时，旧记忆自动标 `pending` |
| `--scene` | work/life/study | - | 场景分类（需求 8.1.1），写入 `context.scene`，检索时同场景加权 |
| `--encrypt` | flag | off | 端侧加密（需求 3.5），需配合 `--secret`；启用后持久化该用户加密开关 |

返回：

```json
{"ok": true, "id": "m_...", "merged": false, "tier": "short",
 "subtype": "general", "links": 1, "auto_conflicts":
 [{"old_id": "m_...", "kind": "numeric", "new_status": "pending", "reason": "50 万 vs 80 万（万）"}],
 "summary": "..."}
```

## 2. search — 联想式检索

链路：**四路召回（倒排/时间/标签实体/图谱）→ 多维加权打分 → 图谱联想扩展 → 访问统计强化**。

```
python neuromem.py search --user <id> --query "张三喝什么咖啡" [options]
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--query` | string | 必填 | 查询内容 |
| `--top-k` | int | 10 | 返回条数 |
| `--tier` | working/short/long | 全部 | 层级过滤 |
| `--subtype` | 四子类型 | 全部 | 子类型过滤 |
| `--tags` | list | - | 标签过滤（需全含） |
| `--from-date` / `--to-date` | ISO | - | 时间范围过滤 |
| `--include-archived` | flag | off | 含归档记忆（唤醒冷记忆） |
| `--no-expand-graph` | flag | off | 关闭图谱联想扩展 |
| `--min-score` | float | 0.05 | 最低相关分 |
| `--no-cache` | flag | off | 关闭前置缓存拦截（默认开启，需求 3.2/8.1.2 enable_cache） |
| `--context-json` | JSON | - | 情境加权，如 `{"scene":"出差"}` |
| `--complete-fragments` | flag | off | 碎片记忆沿图谱/共享标签推测补全并标注 |

特殊行为：**前置缓存拦截**（默认开启）——查询先打三级语义缓存，命中直接返回 `{"ok": true, "from_cache": true, "cache_tier": "...", "answer": ...}`，零大模型调用；`--no-cache` 跳过拦截。查询特征不足（无英文/数字且含"什么/多少/怎么/记得"等泛化词、或长度 ≤2）时，自动切换为「近期重要记忆」时间倒序兜底，返回 `"broad": true`。

## 3. get / versions / update / restore — 单条记忆操作

```
python neuromem.py get --user <id> --id m_...            # 读单条（支持 --secret 解密）
python neuromem.py versions --user <id> --id m_...       # 版本历史（写时复制快照列表）
python neuromem.py update --user <id> --id m_... --text "新内容" [--importance] [--tags]   # 更新（自动留快照）
python neuromem.py restore --user <id> --id m_... --version 1   # 回滚（回滚前也留快照）
```

## 4. correct / contradictions — 错误修正与矛盾检测

```
python neuromem.py correct --user <id> --id m_... --text "正确内容" [--secret]
```

修正链路：旧记忆标 `invalid` + 版本快照 + 移入 archive（物理保留）→ 写新记忆并 `links` 串联修正链 → **自动联动失效相关缓存条目（防脏缓存）** → 重建索引 → 审计。加密记忆修正需 `--secret`，修正内容同样加密写入。

```
python neuromem.py contradictions --user <id>
```

全库扫描**三类矛盾**（需求 3.3）：**数值断言冲突**（同单位不同值）、**时间线冲突**（同维度显式时间点不同，如"2026年3月上线 vs 2026年6月上线"，且至少一句含时间动词）、**情感/断言极性冲突**（喜欢vs不喜欢、是vs不是等）。

## 5. consolidate — 记忆巩固（模拟睡眠期整理）

```
python neuromem.py consolidate --user <id> [--importance-threshold 0.6] [--access-threshold 3] [--min-age-days 7] [--auto-resolve]
```

- 短时记忆满足任一条件（重要 ≥0.6 / 被访问 ≥3 次 / 存在 ≥7 天）→ 迁入长时（按实体情景、纯事实语义、流程类程序自动定子类型），迁移前与长时库查重合并。
- `--auto-resolve`：被 ≥2 次新信息否定的 `pending` 记忆自动标 `invalid` 归档。
- 最后从长时语义/情景记忆提炼用户画像（偏好 + 高频实体）写入 `config.json`。

## 6. forget / demote — 选择性遗忘（永不物理删除）

```
python neuromem.py forget --user <id> --id m_...     # 归档：移入 archive，search 默认不返回，可 --include-archived 唤醒
python neuromem.py demote --user <id> --id m_... --factor 0.5   # 降权：检索权重乘 factor，模拟遗忘曲线
```

## 7. cache — 三级语义缓存（成本优化）

```
python neuromem.py cache --user <id> query --query "..."         # 命中免大模型调用；hot→warm→cold 逐级查，命中自动升温
python neuromem.py cache --user <id> store --query "..." --answer "..." [--source "workbuddy"]
python neuromem.py cache --user <id> invalidate --query "关键词"   # 记忆修正后联动失效，防脏缓存
python neuromem.py cache --user <id> stats                        # 各级条目数/命中次数
```

命中阈值（相似度）：hot 0.90 / warm 0.85 / cold 0.80。命中返回 `{"hit": true, "tier": "warm", "answer": "...", "source": "..."}`。

## 8. sync — 增量同步

```
python neuromem.py sync --user <id> --remote-dir <远端根目录> [--direction up|down|bidirectional] [--dry-run]
```

按每条记忆 `(version, updated_at)` 元组比较，新者胜；只传输差异条目（增量）。`--direction` 控制同步方向（需求 8.2.1 sync_direction）：`up` 仅推送本地→远端，`down` 仅拉取远端→本地，`bidirectional` 双向。`--dry-run` 先预览 push/pull 计划。archive/invalid 记忆默认不参与同步。

## 9. batch — 批量操作（需求 8.2.3）

```
python neuromem.py batch --user <id> --ids m1,m2,m3 --operation <动作> [--params '{"key":"value"}']
```

| 动作 | 说明 | params |
|------|------|--------|
| `delete` | 批量删除（软删：标 invalid 归档，可唤醒） | - |
| `tag` | 批量标记分类（合并标签） | `{"tags":["工作"]}` |
| `importance` | 批量调整重要性 | `{"value":0.8}` |
| `export` | 批量导出到文件 | `{"output":"path.json"}` |

返回 `{"ok": true, "operation": ..., "succeeded": N, "failed": M, "failures": [...]}`。

## 10. stats / profile / audit / export / import

```
python neuromem.py stats --user <id>    # 总量、分层/状态/子类型分布、版本快照数、画像规模
python neuromem.py profile --user <id>  # 画像：preferences + top_entities（元记忆：知道自己知道什么）
python neuromem.py audit --user <id> [--limit 20]   # 审计日志（add/correct/forget/consolidate/cache/sync 全留痕）
python neuromem.py export --user <id> [--output epoch.json]   # 全量备份（含 archived 与 config）
python neuromem.py import --user <id> --file epoch.json       # 导入（自动换新 ID，不覆盖现有）
```

## 11. serve — 本地 HTTP API（跨应用接入）

```
python neuromem.py serve --user <id> [--host 127.0.0.1] [--port 17890]
```

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/search?query=...&top_k=10&include_archived=false&enable_cache=true` | GET | 联想检索（默认前置缓存拦截，`enable_cache=false` 关闭） |
| `/api/stats` | GET | 全维度统计（总量/分层/状态/缓存命中/画像规模/加密状态，需求 8.2.2） |
| `/api/add` | POST | `{text, tier?, subtype?, tags?, entities?, importance?, session_id?, context?}` |
| `/api/correct` | POST | `{id, text}` |
| `/api/forget` | POST | `{id}` |
| `/api/cache` 或 `/api/cache/query` | POST | 语义缓存查询（需求 8.1.3）：`{query}` → `{hit, tier, answer, source}` |
| `/api/cache/store` | POST | 写缓存：`{query, answer, source?}` |
| `/api/cache/invalidate` | POST | 按关键词失效缓存：`{query}` |

仅建议绑定 `127.0.0.1`（内置简单鉴权建议由上层反代/Token 实现）。

---

## 分场景命令示例

### 场景 A：会话中记住用户偏好

```
python neuromem.py add --user alice --text "用户偏好深色主题界面" --tags 偏好 UI --importance 0.7
python neuromem.py add --user alice --text "项目 Alpha 使用 React + Rust 技术栈，周例会每周三 15:00" --tags 项目 --entities '{"project":["Alpha"]}' --importance 0.8
```

### 场景 B：用户问"我之前说过什么"

```
python neuromem.py search --user alice --query "我之前说过什么"
# broad:true → 近期重要记忆倒序兜底
```

### 场景 C：发现新信息与旧记忆矛盾

```
python neuromem.py add --user alice --text "项目 Alpha 预算调整为 80 万元" --auto-conflict
# 返回 auto_conflicts 列出旧记忆（标 pending 待验证）
python neuromem.py contradictions --user alice   # 全库复查
python neuromem.py correct --user alice --id m_old --text "预算最终为 75 万元"   # 用户确认后修正
python neuromem.py cache --user alice invalidate --query "预算"   # 联动失效旧缓存
```

### 场景 D：端侧降本（重复问答拦截）

```
python neuromem.py cache --user alice store --query "公司 VPN 怎么连" --answer "<标准操作流程>" --source provisioning
python neuromem.py cache --user alice query --query "VPN 怎么连接"     # 相似命中 → zero Token
```

### 场景 E：每日/会话末巩固

```
python neuromem.py consolidate --user alice --auto-resolve
python neuromem.py profile --user alice        # 画像已随巩固刷新
```

### 场景 F：跨端同步与备份

```
python neuromem.py sync --user alice --remote-dir "D:/sync/neuromem" --dry-run
python neuromem.py sync --user alice --remote-dir "D:/sync/neuromem"
python neuromem.py export --user alice --output "backup-$(date +%F).json"
```

---

# v2.0 新增命令

> v2.0 的全部新增能力均为「可选增强」：不安装任何依赖、不配置任何环境变量时，仍保持 v1.1.0 的完整可用性。

## 21. capture — 自动捕获（RQ-03）

从一段对话/会议记录中自动提炼关键事实并逐条写入记忆。

```
python neuromem.py capture --user <id> --text "对话原文" [--tags 标签...]
python neuromem.py capture --user <id> --file meeting.txt
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--text` | string | "" | 对话/会议原文（与 `--file` 二选一） |
| `--file` | path | "" | 从文件读取原文 |
| `--tags` | string[] | - | 附加到所有捕获条目的标签 |

提炼规则（零依赖）：按句号/换行切句 → 长句再按逗号拆分（>20 字）→ 信号打分（偏好/决策/数字/专名/项目词，≥2 分入选）→ 去重 → 最多 10 条。

返回：`{"ok": true, "captured": 3, "facts": [...], "dedup_merged": 0, "ids": [...]}`

## 22. mcp — MCP stdio 服务器（RQ-04）

```
python neuromem.py mcp --user <id>
```

启动后按 JSON-RPC 逐行收发，支持：

| 方法 | 说明 |
|------|------|
| `initialize` | 返回 `serverInfo`（name / version）与 capabilities |
| `tools/list` | 列出 `memory_add` / `memory_search` / `memory_correct` / `memory_stats` 四个工具及入参 schema |
| `tools/call` | 调用工具，`arguments` 与 CLI 参数同义（如 `{"text": "...", "importance": 0.7}`） |
| `ping` | 健康检查 |

客户端配置（WorkBuddy / Claude / Cursor）：

```json
{"mcpServers": {"neuro-memory": {"command": "python",
  "args": ["<技能目录>/scripts/neuromem.py", "mcp", "--user", "default"]}}}
```

## 23. graph — 实体关系图谱（RQ-05）

```
python neuromem.py graph --user <id> [--entity 张三]
```

返回 `{"ok": true, "nodes": 8, "edges": 3, "sample_nodes": [...], "sample_edges": [...], "entity": {...}}`。
带 `--entity` 时额外返回该实体的邻居节点与关系边。图谱在 `add` / `capture` / `correct` 时自动增量维护。

## 24. evolve — 自我进化（RQ-07）

```
python neuromem.py evolve --user <id>
```

逻辑：读取 `config.json` 中的 `correction_rules`（每次 `correct` 自动沉淀）→ 按主题实体（人名/项目名/专名）聚类 → 同一主题纠偏 ≥2 次则合成一条程序性记忆（`subtype=procedural`, `tags=["纠偏规则","auto-evolved"]`）→ 重建索引并留审计。

返回：`{"ok": true, "evolved": 1, "rules": [{"rule": "...", "count": 2, "mem_id": "..."}], "total_corrections": 2}`

## 25. benchmark — 基准跑分（RQ-11）

```
python neuromem.py benchmark --user <id>
```

内置 19 组评测用例，覆盖 6 类场景：语义检索（同义改写）、偏好与身份、数字与配置、项目与业务事实、矛盾修正（数值）、时间线矛盾。

返回：

```json
{"ok": true, "benchmark": {"accuracy": 100.0, "recall": 100.0, "queries": 19,
  "query_hits": 19, "total_ms": 1316.8, "avg_query_ms": 35.9, "total_query_ms": 682.1,
  "engine_version": "2.0.0"}, "scenarios": [...]}
```

`avg_query_ms` 仅统计检索耗时（不含写入）。外部评测集（LoCoMo / LongMemEval）可用同样方式接入。

---

# v2.0 既有命令的行为升级

| 命令 | 升级点 |
|------|--------|
| `search` | 语义分拆为三路互补：字面 bigram（0.45）+ 512 维哈希向量余弦（0.15）+ BM25 关键词（0.10，归一化后），叠加重要性 0.15 / 频次 0.05 / 新近 0.05 / 强度 0.05 + 情境加成（≤0.12），整体乘遗忘权重 |
| `correct` | 纠偏时自动沉淀 `correction_rules`（主题 + 旧表述 + 新表述 + 时间），后续同主题检索按规则加权，实现"纠正一次不再犯" |
| `add --encrypt` | 密文格式升级为 `enc:v2`（encrypt-then-MAC，HMAC-SHA256 认证）；`get/correct/update` 同时兼容 `enc:v1` 存量数据；密文被篡改时返回 `{"ok": false, "error": "decrypt failed: ciphertext integrity check failed"}` |
| `consolidate` | 新增 `--daemon` / `--interval-min`（默认 30）/ `--busy-window`（默认 5 分钟）：守护模式定时巩固，检测到窗口内有写入则自动顺延，避免与在线写入争抢 |
| `serve` | 新增 `GET /ui`（内嵌可视化面板，零前端依赖）与 `GET /api/ui/stats`（时间线/分层分布/缓存命中/图谱统计） |
