# 🧠 米乐-神经网络记忆（Mile Neuro Memory）

> 给 AI 助手装一套**类脑记忆系统**：三层记忆编码 + 遗忘曲线 + 混合召回 + 矛盾修正 + 语义缓存。
> **零第三方依赖**，纯 Python 标准库，离线可用。

![version](https://img.shields.io/badge/version-2.0.0-blue)
![python](https://img.shields.io/badge/python-3.8%2B-blue)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![tests](https://img.shields.io/badge/tests-85%2F85%20passed-brightgreen)
![benchmark](https://img.shields.io/badge/评测-91%2F100%20·%20第1名-orange)
![license](https://img.shields.io/badge/license-MIT-green)

### 📄 [**在线综合评测报告 → pz857082775.github.io/mile-neuro-memory**](https://pz857082775.github.io/mile-neuro-memory/)

17 个主流记忆方案 × 20 项能力横向测评，含逐项给分理由、扣分证据与实测数据。也可以直接看源码：[`docs/index.html`](docs/index.html)。

---

## 📊 综合评测：91 / 100，17 个记忆方案中排名第 1

本项目附带一份**完整的能力测评报告**，用统一口径（20 项能力 × 5 分 = 100 分）横向对比了 17 个主流记忆方案——包括 Mem0、Cognee、Letta/MemGPT、EverMemOS、MindMemOS（华为诺亚）、JiuwenMemory（华为 openJiuwen）、agentmemory、memU、MemPalace 等开源项目与本机方案。

**📄 在线报告：[pz857082775.github.io/mile-neuro-memory](https://pz857082775.github.io/mile-neuro-memory/)** — 浏览器打开即为可视化报告（排行榜 / 20 项能力矩阵 / 逐项提升证据 / 实测数据）。

### 总分排行（20 项 × 5 分）

| 名次 | 方案 | 总分 | 说明 |
|:---:|---|---:|---|
| 🥇 **1** | **mile-neuro-memory v2.0（本项目）** | **91** | 12 项满分 |
| 🥈 2 | agentmemory | 66 | 29k ⭐，插件式自动捕获 |
| 🥈 2 | MindMemOS | 66 | 华为诺亚，OS 式分层记忆 |
| 4 | mile-brain-memory | 64 | 本项目 v1.0 前身 |
| 5 | JiuwenMemory | 61 | 华为 openJiuwen |
| 6 | Mem0 | 55 | 55k ⭐，业界最常见 |
| 6 | Cognee | 55 | 17.8k ⭐，图谱导向 |
| 8 | EverMemOS | 48 | EverMind |
| 9 | ReMe | 46 | agentscope-ai |
| 10 | memU | 41 | OpenClaw，8.8k ⭐ |
| 11–17 | OpenClaw memory-upgrade · Letta/MemGPT · memory-manager-v2 · hy-memory · memory-evolution · MemPalace · memory-management | 16–35 | — |

### 20 项能力中拿到满分的 12 项

| 能力 | 分数 | 关键实现 |
|---|:---:|---|
| 分层记忆（工作/短时/长时） | 5 | 三层编码 + 重要性/频次/时效自动迁移 |
| 关键词检索 | 5 | 真实 BM25（idf + 词频饱和 + 长度归一化） |
| 矛盾检测与修正 | 5 | 数值 / 时间线 / 极性三类矛盾扫描 |
| 版本管理与回滚 | 5 | 每次修改留快照，`restore` 可回溯 |
| 遗忘机制 | 5 | 遗忘曲线降权 + 归档不删除，可唤醒 |
| 语义缓存降本 | 5 | 热/温/冷三级缓存，命中免大模型调用 |
| 增量同步 | 5 | 版本+时间戳双向增量，支持 dry-run |
| 多租户隔离 | 5 | 每用户独立目录，服务仅监听 127.0.0.1 |
| 全链路审计 | 5 | `audit.jsonl` 逐操作留痕 |
| 后台自动巩固 | 5 | `consolidate --daemon` + 忙时退避 |
| 接入方式 | 5 | CLI + HTTP API + MCP 三通道 |
| 零依赖 | 5 | 纯标准库，无外网可运行 |

### 还差什么（诚实披露）

| 短板 | 分数 | 原因 |
|---|:---:|---|
| 向量语义检索 | 4 | hashing 向量偏词形，非真语义嵌入；无 ANN，万级数据会退化 |
| 知识图谱关系 | 4 | 有节点与共现边，但无关系类型、无多跳推理 |
| 端侧加密 | 4 | `enc:v2` 是 encrypt-then-MAC 自研构造，能检篡改但非标准 AEAD，未经密码学审计 |
| 自动捕获 | 4 | 需上层调用 `capture` 传文本，不是会话生命周期钩子 |
| 可视化面板 | 4 | `/ui` 只读展示，不支持面板内增删改 |
| 自主进化 | 4 | 只从纠偏历史提炼规则，不覆盖其他失败场景 |
| 反馈闭环 | 4 | 靠 `correct` 隐式沉淀，无显式 👍/👎 入口 |
| 公共基准背书 | 3 | 内置 19 组为自建合成集，未接入 LoCoMo / LongMemEval |

> ⚠️ **口径声明**：91 分是**本仓库自定义的 20 项能力评分**，不是行业标准测评分数。换一套加权体系（比如把"公共基准"权重调高），排名结论可能不同。其余 16 个方案的分数基于公开文档与本机静态分析，**未逐一本地运行**。报告里每一项都写明了给分理由和扣分证据，欢迎自行复核：[`docs/index.html`](docs/index.html)。

---

## ⚡ 30 秒上手

无需 pip install，无需 API Key，无需联网：

```bash
# 写入一条记忆
python scripts/neuromem.py add --user me --text "张三喜欢喝美式咖啡，常去公司楼下瑞幸" --importance 0.7

# 换个说法也能找回来（同义改写召回）
python scripts/neuromem.py search --user me --query "常去瑞幸买咖啡的人是谁"

# 从会议记录自动提炼事实
python scripts/neuromem.py capture --user me --text "项目 Alpha 预算调整为 80 万元，王五负责前端，用户偏好深色主题"

# 打开可视化面板
python scripts/neuromem.py serve --user me --port 8765
# → 浏览器访问 http://127.0.0.1:8765/ui
```

所有命令统一输出 JSON，方便 AI 助手直接解析。

---

## ✨ 核心特性

### 类脑三层记忆

```
工作记忆（瞬时） ──► 短时记忆 ──► 长时永久记忆
        │                │              │
     会话上下文      默认写入层      高频/高重要性
                    半衰期 14 天     半衰期 60 天
```

引擎按 **重要性 × 访问频次 × 时效衰减** 自动迁移，配合遗忘曲线（`demote`）模拟"不用就淡忘"，但归档永不物理删除。

### 混合召回（三层打分融合）

```python
sem   = max(字面bigram相似度, 标签相似度×0.8, 实体相似度×0.7)
vec   = cos(512维哈希TF向量, ...)          # 换个说法也能撞上
bm25  = Σ idf(t) · tf饱和 · 长度归一化       # 关键词精确命中
score = (0.45·sem + 0.15·vec + 0.10·bm25
         + 0.15·重要性 + 0.05·频次 + 0.05·新近 + 0.05·强度
         + 情境加成≤0.12) × 遗忘权重
```

三路互补：字面管"原话复述"，向量管"换个说法"，BM25 管"关键词命中且长文档不算亏"。

### 纠偏一次，同类不再犯

```
correct(旧 → 新)
  ├─► 旧记忆 status=invalid + 版本快照（可回溯）
  ├─► 新记忆 links=[旧ID]（纠偏链）
  ├─► 失效相关缓存（防脏缓存）
  └─► 沉淀 correction_rules{topic, old, new}
              │
              ▼ 后续检索
      新表述 ×1.15 加权 / 同主题旧表述 ×0.60 降权
              │
              ▼ evolve（同主题纠偏≥2次）
      合成 procedural 规则记忆："关于X，历史纠正N次，以最近一次为准"
```

### 语义缓存直接省 Token

回答前先查缓存：热（精确命中）→ 温（语义相近）→ 冷（模糊匹配），命中即返回，**完全不调用大模型**。

### 认证加密

```
enc:v2:<base64( HMAC-SHA256(32) | salt(16) | nonce(8) | ciphertext )>
                 └─ 覆盖 salt+nonce+ciphertext，先验后解 ┘
```

改密文任意 1 字节即报 `ciphertext integrity check failed`；密钥错误与密文篡改返回同一错误，不泄露区分信息。向后兼容旧版 `enc:v1`。

---

## 🔌 接入方式

### 1. 作为 AI 技能安装（WorkBuddy / Claude Code / OpenClaw）

把整个仓库目录放到技能目录下即可，`SKILL.md` 已含完整触发规则与工作流：

```bash
git clone https://github.com/pz857082775/mile-neuro-memory.git ~/.workbuddy/skills/mile-neuro-memory
```

### 2. 作为 MCP 服务器（Claude Desktop / Cursor / 任意 MCP 客户端）

```json
{
  "mcpServers": {
    "neuro-memory": {
      "command": "python",
      "args": ["/绝对路径/mile-neuro-memory/scripts/neuromem.py", "mcp", "--user", "default"]
    }
  }
}
```

提供 `memory_add` / `memory_search` / `memory_correct` / `memory_stats` 四个工具。

### 3. 作为 HTTP API（任意语言）

```bash
python scripts/neuromem.py serve --user me --port 8765
# /api/add  /api/search  /api/correct  /api/forget  /api/stats  /api/health
# /ui       可视化面板
# /api/ui/stats  面板数据
```

---

## 📖 命令总览（25 个）

| 分类 | 命令 |
|---|---|
| **写入** | `add` · `capture`（自动提炼） · `import` |
| **读取** | `search` · `get` · `profile` · `stats` · `graph` |
| **修正** | `correct` · `update` · `contradictions` · `versions` · `restore` |
| **遗忘** | `forget`（归档） · `demote`（降权） |
| **维护** | `consolidate`（含 `--daemon` 后台） · `evolve` · `batch` |
| **缓存** | `cache query/store/invalidate/stats` |
| **数据** | `sync` · `export` · `audit` |
| **服务** | `serve` · `mcp` |
| **评测** | `benchmark` |

完整参数规格见 [`references/api-reference.md`](references/api-reference.md)，架构设计见 [`references/architecture.md`](references/architecture.md)。

---

## 🧪 测试与基准

```bash
# 基线回归（63 项）
python tests/test_neuromem.py $(which python) ./scripts tests/results-v2-base.json

# v2.0 能力专项验收（22 项）
python tests/test_v2_rqs.py $(which python) ./scripts tests/results-v2-rqs.json

# 内置基准跑分（19 组 / 6 类场景）
python scripts/neuromem.py benchmark --user bench
```

### 实测结果

| 指标 | v1.1.0 | v2.0.0 |
|---|---:|---:|
| 回归测试 | 63 / 63 | **63 / 63**（零回归） |
| 专项验收 | — | **22 / 22** |
| 基准召回 | — | **19 / 19 = 100%** |
| 平均检索耗时（200 条记忆） | 674.9 ms | **573.3 ms（-15.1%）** |
| 单查询平均耗时 | — | 33.5 ms |
| 子命令数 | 20 | 25 |
| 第三方依赖 | 0 | **0** |

原始数据：[`tests/perf-v1-vs-v2.json`](tests/perf-v1-vs-v2.json) · [`tests/results-v2-rqs.json`](tests/results-v2-rqs.json)

---

## ⚙️ 配置（全部可选）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `NEUROMEM_DIR` | `~/.neuromem` | 记忆数据根目录 |
| `NEUROMEM_LLM_API_KEY` | 空 | 可选：LLM 摘要提炼（OpenAI 兼容） |
| `NEUROMEM_LLM_BASE_URL` | `https://api.openai.com/v1` | LLM 服务地址 |
| `NEUROMEM_LLM_MODEL` | `gpt-4o-mini` | 提炼模型 |
| `NEUROMEM_EMBED_BASE_URL` | 空 | 可选：远程 Embedding，空则用内置哈希向量 |
| `NEUROMEM_EMBED_MODEL` | `text-embedding-3-small` | Embedding 模型 |
| `NEUROMEM_EMBED_API_KEY` | 空 | Embedding 密钥 |

**不配任何变量 = 完整可用**。所有 v2.0 能力都是"可选增强"，外部服务调用失败会静默回退到内置实现，检索不中断。

---

## 📁 目录结构

```
mile-neuro-memory/
├── SKILL.md                  # 技能定义：触发条件、工作流、异常策略
├── _skillhub_meta.json       # 技能市场元数据（中英双语）
├── scripts/
│   └── neuromem.py           # 引擎本体（单文件，零依赖，25 命令）
├── references/
│   ├── api-reference.md      # 25 个命令的完整参数规格
│   └── architecture.md       # 分层模型 / 索引结构 / 打分公式 / 加密格式
├── docs/
│   ├── index.html            # ★ 综合评测报告（GitHub Pages 首页）
│   ├── benchmark-data.json   # 17 方案 × 20 维度原始评分
│   └── roadmap-v2.md         # v2.0 开发需求清单与实施计划
└── tests/
    ├── test_neuromem.py      # 63 项基线回归
    ├── test_v2_rqs.py        # 22 项 v2.0 能力专项验收
    ├── perf_compare.py       # v1 vs v2 性能对比
    └── *.json                # 实测结果原始数据
```

---

## 🗺️ 路线图

- [x] **v1.0** — 三层记忆、混合检索、矛盾修正、缓存、同步、加密、审计
- [x] **v1.1** — 版本快照、批量操作、情境匹配、多租户隔离
- [x] **v2.0** — 向量召回、BM25、自动捕获、MCP、图谱、反馈闭环、自我进化、可视化面板、后台巩固、认证加密、基准跑分
- [ ] **v2.1** — 接入 LoCoMo / LongMemEval 公共评测集
- [ ] **v2.1** — 会话生命周期钩子（真正"零调用自动捕获"）
- [ ] **v2.2** — 标准 AEAD（AES-GCM / ChaCha20-Poly1305，cryptography 可选依赖）
- [ ] **v2.2** — 图谱关系类型与多跳推理
- [ ] **v2.3** — 万级数据 ANN 索引（HNSW / LSH）

详见 [`docs/roadmap-v2.md`](docs/roadmap-v2.md)。

---

## 📄 License

[MIT](LICENSE) © 2026 米乐

## 🙏 致谢

评测报告中的对比方案均属各自作者：Mem0、Cognee、Letta/MemGPT、EverMemOS、MindMemOS、JiuwenMemory、agentmemory、memU、MemPalace、ReMe、OpenClaw 及各本机方案。本仓库仅做横向能力对照，不代表对其实现的完整评价。
