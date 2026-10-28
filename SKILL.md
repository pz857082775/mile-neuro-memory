---
name: mile-neuro-memory
display_name: 米乐-神经网络记忆
display_name_en: Mile Neuro Memory
description: 类脑神经网络记忆技能（NeuroMem v2.0）。当 AI 助手需要记住用户信息、跨会话复用上下文、检索历史对话、修正错误记忆、去重/巩固长期记忆、降低大模型 Token 成本时使用。典型触发场景：用户提供了个人信息/偏好/项目事实/工作流程需要长期记住；用户问「我之前说过……」「你还记得吗」；需要把历史对话自动提炼成结构化记忆；对话中出现前后矛盾的信息需要检测修正；大量重复性问答需要语义缓存拦截。使用本技能运行 scripts/neuromem.py 引擎（零第三方依赖，Python 标准库实现），实现三层记忆、向量语义+BM25 混合召回、自动捕获、矛盾检测与版本化修正、反馈闭环自我进化、实体关系图谱、三级语义缓存、增量同步、AEAD 认证加密、全链路审计，并内置 MCP 服务器与 Web 可视化面板。
description_zh: 模拟人脑「工作记忆→短时→长时永久」三层编码与遗忘曲线，提供向量语义+BM25 混合检索、自动捕获对话事实、矛盾自动检测与版本化修正、反馈闭环自我进化、实体关系图谱、三级语义缓存降本、增量跨端同步、AEAD 认证加密与全链路审计。零第三方依赖，纯 Python 标准库实现，离线可跑，25 个子命令，内置 MCP 服务器与可视化面板。
description_en: Brain-inspired memory system with working/short-term/long-term tiers, hybrid vector-semantic + BM25 retrieval, automatic conversation fact capture, contradiction detection with versioned correction, feedback-loop self-evolution, entity-relation graph, three-tier semantic cache for token savings, incremental sync, authenticated AEAD encryption and full audit trail. Zero third-party dependencies, pure Python stdlib, works fully offline, 25 subcommands, built-in MCP server and web dashboard.
category: 记忆与知识管理
version: 2.0.0
author: 米乐
user-invocable: true
disable-model-invocation: false
allowed-tools:
  - Read
  - Write
  - Bash
  - PowerShell
---

# 米乐-神经网络记忆（Mile Neuro Memory）

## Overview

NeuroMem 是一套模拟人脑记忆机制的通用记忆系统，供 AI 助手跨会话、跨应用记住用户信息。它把记忆分成「瞬时工作记忆 → 短时记忆 → 长时永久记忆」三层，配合倒排索引 + 关联图谱 + 语义相似度的混合检索，并原生内嵌「热/温/冷」三级语义缓存，命中即免大模型调用。全部能力由 `scripts/neuromem.py` 提供（Python 标准库实现，**零第三方依赖**，可在离线沙箱直接运行）。

### v2.0 新增能力（全部为"可选增强"，不装任何依赖也能用）

| 能力 | 命令 | 说明 | 无依赖回退 |
|------|------|------|-----------|
| 向量语义检索 | `search` 内置 | 哈希向量 + 余弦相似度，与字面相似度取长补短 | 内置哈希向量；配 `NEUROMEM_EMBED_*` 可切远程 Embedding |
| BM25 关键词 | `search` 内置 | 词频饱和度 + 文档长度归一化，长文档不再被短句压分 | 纯标准库实现，始终可用 |
| 自动捕获 | `capture` | 从对话/会议记录自动提炼关键事实并写入（按句+逗号拆分，信号打分） | 规则引擎，无需 LLM |
| MCP 接入 | `mcp` | stdio MCP 服务器，Claude/Cursor/WorkBuddy 等客户端即插即用 | 无客户端时不影响 CLI |
| 实体关系图谱 | `graph` | 实体节点 + 共现关系边，支持按实体查询邻居 | 规则抽取；可接 `NEUROMEM_LLM_*` 做深度抽取 |
| 反馈闭环 | `correct` 内置 | 每次纠偏自动沉淀「纠偏规则」并参与后续排序加权 | 本地规则库 |
| 自我进化 | `evolve` | 同一主题多次纠偏 → 自动生成程序性记忆规则（可审计） | 频次统计 + 规则合成 |
| 可视化面板 | `serve` → `/ui` | 内置 Web 面板：记忆时间线、分层分布、缓存命中、图谱 | 纯 HTML/JS，零前端依赖 |
| 后台自动巩固 | `consolidate --daemon` | 定时巩固 + 忙时退避（有写入则顺延） | 前台 `consolidate` 不受影响 |
| 认证加密 | `add --encrypt` | `enc:v2` = 密钥流 + HMAC 认证（encrypt-then-MAC），可检出篡改；兼容旧 `enc:v1` | 纯标准库 hmac |
| 基准跑分 | `benchmark` | 内置 19 组同义改写/矛盾修正评测集，输出准确率与平均检索耗时 | 纯本地合成集 |


## 何时使用（触发条件）

满足以下任一情况，立即加载本技能：

- 用户透露需要长期记住的信息：个人偏好、事实、习惯、项目背景、工作流程、关键数字。
- 用户询问历史信息：出现"我之前说过 / 你还记得吗 / 上次聊的 / 帮我回忆"等表达。
- 需要对对话内容做结构化沉淀：把会话要点提炼成可检索的记忆条目。
- 检测到信息矛盾：用户前后说法不一致，或新信息与已存记忆冲突。
- 重复性问答频繁：同类问题反复出现，需要缓存拦截省 Token。
- 跨设备/跨应用同步记忆：多端共用同一份记忆。
- 用户要求管理记忆：查看统计、导出备份、修正错误记忆、清理归档。

不需要本技能：纯一次性闲聊、与用户记忆无关的通用知识问答。

## 核心工作流（AI 助手使用路径）

```text
会话开始 ──► 1. 载入画像（profile）获取用户概况
              │
对话进行中 ──► 2. 每次收到新信息 → add 写入（自动分层/去重/建索引）
              │   每次回答前 → 先 cache query（命中则免单），
              │   未命中 → search 联想检索（多路召回）→ 组织回答
              │   发现前后矛盾 → contradictions 检测 / correct 修正
              │
会话结束 ──► 3. consolidate 巩固（短时→长时迁移、画像提炼）
              │
定期 ──► 4. sync 同步（本地↔云端目录）、audit 审计、export 备份
```

### 分步执行说明

1. **载入画像**：对话开始时运行 `neuromem.py profile --user <user>`，把返回的 `preferences` 与 `top_entities` 作为对话基础上下文。画像为空则跳过。
2. **写入记忆**：用户每透露一条可沉淀信息，调用 `add`。让引擎自动完成：分层（默认短时）→ 去重合并 → 倒排索引更新 → 图谱建边 → 可选矛盾检测。重要程度越高的信息设置越高的 `--importance`（0-1）。
3. **缓存优先**：回答前先查 `cache query`。命中（相似度 ≥ 阈值）直接返回缓存答案，不调用大模型；未命中再走记忆检索。
4. **记忆检索**：`search` 执行四路混合召回 + 多维加权排序 + 图谱联想扩展。宽泛查询（"我之前说过什么"）自动返回近期重要记忆兜底。
5. **矛盾处理**：新信息与旧记忆冲突时，用 `add --auto-conflict` 自动把旧记忆标 `pending`（待验证）；用户明确纠正时用 `correct`（旧记忆标 `invalid` 并留痕，写入新版本）。
6. **记忆巩固**：每日/会话末运行 `consolidate`，短时高频重要记忆升级长时，去重合并，提炼用户画像。
7. **同步备份**：多端场景用 `sync --remote-dir <路径>` 增量双向同步；定期 `export` 全量备份。

## 核心操作速查

所有命令统一格式：`python neuromem.py <command> [options]`，输出统一为 JSON。

| 操作 | 命令 | 关键参数 | 说明 |
|------|------|----------|------|
| 写入记忆 | `add` | `--text/--file --tier --tags --entities --importance --session --context-json --auto-conflict --scene --encrypt` | 自动分层/去重/索引/建图；`--scene work/life/study` 场景分类；`--encrypt` 配合 `--secret` 端侧加密 |
| 联想检索 | `search` | `--query --top-k --tier --subtype --tags --from-date --to-date --include-archived --context-json --complete-fragments --no-cache` | 四路召回+多维加权+图谱扩展；默认前置缓存拦截，`--no-cache` 关闭 |
| 读单条 | `get` | `--id [--secret]` | 按 ID 读（加密记忆需 secret 解密） |
| 修正错误 | `correct` | `--id --text [--secret]` | 旧记忆标 invalid 留痕，写新版本；自动联动失效相关缓存 |
| 更新记忆 | `update` | `--id --text --importance --tags [--secret]` | 自动留版本快照；加密记忆需 secret |
| 版本历史 | `versions` | `--id` | 查看修改历史 |
| 回滚版本 | `restore` | `--id --version` | 回滚到历史版本 |
| 矛盾检测 | `contradictions` | 无 | 全库扫描三类矛盾：数值/时间线/极性 |
| 记忆巩固 | `consolidate` | `--importance-threshold --access-threshold --min-age-days --auto-resolve` | 短时→长时迁移+画像提炼 |
| 归档遗忘 | `forget` | `--id` | 归档非删除，可唤醒 |
| 降权遗忘 | `demote` | `--id --factor` | 模拟遗忘曲线 |
| 批量操作 | `batch` | `--ids --operation delete/tag/importance/export --params` | 批量删除（软删）/打标/调重要性/导出（需求8.2.3） |
| 语义缓存 | `cache` | `query/store/invalidate/stats` | 三级缓存，命中免大模型调用 |
| 增量同步 | `sync` | `--remote-dir --direction up/down/bidirectional --dry-run` | 版本+时间新者胜；支持单向/双向 |
| 统计概况 | `stats` | 无 | 分层/状态/版本/画像/缓存统计 |
| 用户画像 | `profile` | 无 | 读取元记忆（偏好+高频实体） |
| 导出备份 | `export` | `--output` | 全量导出 JSON |
| 导入恢复 | `import` | `--file` | 导入备份 |
| 审计日志 | `audit` | `--limit` | 全链路操作留痕 |
| HTTP 服务 | `serve` | `--host --port` | 本地 API；浏览器访问 `/ui` 打开可视化面板，`/api/ui/stats` 为面板数据 |
| 自动捕获 | `capture` | `--text/--file --tags` | 从对话/会议记录自动提炼关键事实写入（v2.0） |
| MCP 服务 | `mcp` | 无（stdio） | 以 MCP 服务器方式对外提供 memory_add/search/correct/stats 工具（v2.0） |
| 图谱查询 | `graph` | `--entity` | 导出实体节点与关系边，或查某实体的邻居（v2.0） |
| 自我进化 | `evolve` | 无 | 从纠偏历史提炼规则，写入程序性记忆（v2.0） |
| 基准跑分 | `benchmark` | 无 | 内置评测集跑分：准确率/召回/平均检索耗时（v2.0） |

完整参数规格、字段说明与命令示例见 `references/api-reference.md`；架构设计（分层模型、索引结构、打分公式、缓存机制）见 `references/architecture.md`。

## 输出格式规范

所有命令输出统一 JSON，结构为：

```json
{
  "ok": true,
  "...": "..."
}
```

关键约定：

- 成功返回 `"ok": true`，失败返回 `"ok": false` 并带 `"error"` 字段（如 `{"ok": false, "error": "memory not found"}`）。
- `search` 返回 `{"ok": true, "results": [...], "broad": true/false}`。`broad: true` 表示命中宽泛查询兜底（查询特征不足时按重要性+新近度返回近期记忆）。每条结果含完整记忆字段 + `_score`（相关度得分），碎片化短记忆在 `--complete-fragments` 时会带 `_completion` 推测补全标注。
- `add` 返回 `id / tier / subtype / merged / links / auto_conflicts`。`merged: true` 表示与已有记忆自动合并；`auto_conflicts` 列出检测到的矛盾（含旧记忆 id 与原因）。
- `cache query` 返回 `{"hit": true, "tier": "hot|warm|cold", "answer": ...}` 或 `{"hit": false}`。命中即免大模型调用。
- `profile` 返回 `preferences`（偏好列表）与 `top_entities`（高频实体）。

### 输出示例（检索）

```json
{
  "ok": true,
  "broad": false,
  "results": [
    {
      "id": "m_1793150228128_4f02f242",
      "summary": "张三喜欢喝美式咖啡，常去公司楼下瑞幸",
      "tier": "short",
      "subtype": "general",
      "tags": ["偏好", "咖啡"],
      "importance": 0.7,
      "status": "active",
      "access_count": 3,
      "_score": 0.82
    }
  ]
}
```

## 异常与边界处理策略

| 异常场景 | 处理策略 |
|----------|----------|
| 检索超时/无结果 | 降低 `--min-score` 重试；宽泛查询用"近期重要记忆"兜底；仍无结果如实告知"记忆中无此信息"，**不要编造** |
| 缓存冲突（同一查询多条矛盾缓存） | 引擎按相似度返回最优单条；人工处理时以最新写入者为准并调用 `correct` 修正 |
| 输入超长 | 先让用户/上层提炼要点，或分段 `add`；引擎对大文本用 `summary` 字段保证核心语义可检索 |
| 记忆修正 | 一律 `correct`（旧标 invalid 留痕）而非直接 `update` 覆盖，保证版本可回溯；correct 自动联动失效相关缓存条目 |
| 网络中断 | 记忆全部本地存储，离线可用；`sync` 失败时重试即可，数据不丢失 |
| 权限/多租户 | 每个用户独立 `--user` 目录，严格隔离；`serve` 模式仅监听 127.0.0.1 |
| 大模型调用失败（LLM 提炼） | `add --llm-summarize` 失败时自动降级为原文摘要，不影响写入 |
| 加密数据 | `add --encrypt --secret` 启用端侧加密并持久化；加密记忆的 `get/correct/update` 均需 `--secret`，丢失密钥则无法解密 |
| 空文本/空查询 | 引擎返回 `{"ok": false, "error": "empty text"}` 或 `broad: true` 兜底，不崩溃 |
| 文件不存在（--file） | 返回 `{"ok": false, "error": "cannot read file: ..."}`，不抛异常 |
| 导入冲突 | `import` 自动分配新 ID 并置 active，避免覆盖现有记忆 |

## 配置与环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `NEUROMEM_DIR` | `~/.neuromem` | 记忆数据根目录（多用户子目录隔离） |
| `NEUROMEM_LLM_API_KEY` | 空 | 可选 LLM 提炼的 API Key（OpenAI 兼容） |
| `NEUROMEM_LLM_BASE_URL` | `https://api.openai.com/v1` | LLM 服务地址（可接任意兼容服务） |
| `NEUROMEM_LLM_MODEL` | `gpt-4o-mini` | 提炼模型名 |
| `NEUROMEM_EMBED_BASE_URL` | 空 | 可选：远程 Embedding 服务（OpenAI 兼容 `/embeddings`），空则用内置哈希向量 |
| `NEUROMEM_EMBED_MODEL` | `text-embedding-3-small` | Embedding 模型名 |
| `NEUROMEM_EMBED_API_KEY` | 空 | Embedding 服务密钥 |

数据目录布局：`<root>/users/<user_id>/` 下分 `config.json`（画像/遗忘策略/加密开关）、`memories/<tier>/<subtype>/`（记忆条目）、`index/`（倒排+图谱）、`versions/`（版本快照）、`archive/`（归档）、`audit.jsonl`（审计日志）、`cache-<tier>.jsonl`（三级缓存）。

## 扩展与复用

- **新记忆子类型**：`SUBTYPES` 常量（当前 `general/episodic/semantic/procedural`）可扩展，分层迁移逻辑自动兼容。
- **接任意 LLM**：`NEUROMEM_LLM_BASE_URL` 指向任意 OpenAI 兼容端点即可启用摘要提炼。
- **跨应用接入**：`serve` 启动本地 HTTP API（/api/add、/api/search、/api/correct、/api/forget、/api/stats、/api/health），任意语言/应用可调；浏览器打开 `http://127.0.0.1:<port>/ui` 即为可视化面板。
- **MCP 接入**：把 `neuromem.py mcp` 注册为 MCP 服务器即可被 AI 客户端直接调用，配置示例：

```json
{
  "mcpServers": {
    "neuro-memory": {
      "command": "python",
      "args": ["<技能目录>/scripts/neuromem.py", "mcp", "--user", "default"]
    }
  }
}
```

- **向量后端可选增强**：设置 `NEUROMEM_EMBED_BASE_URL` / `NEUROMEM_EMBED_MODEL` 后自动调用远程 Embedding（OpenAI 兼容 `/embeddings`）；调用失败自动回退内置哈希向量，检索不中断。
- **云端同步**：把 `--remote-dir` 指向同步盘/对象存储挂载目录即实现云端兜底。
- **检索权重调优**：修改 `_score` 中的语义/重要/频次/新近权重系数即可适配业务场景。
- **零依赖保障**：引擎只用 Python 标准库，离线沙箱、无外网环境可直接运行，不依赖 Embedding 模型（内置中文 bigram 相似度近似语义）。

## 资源

- `scripts/neuromem.py` — 类脑记忆引擎 CLI（25 个子命令，Python 标准库实现，v2.0）
- `references/api-reference.md` — 全部命令参数规格、字段说明、分场景命令示例
- `references/architecture.md` — 分层记忆模型、数据布局、检索打分公式、三级缓存与成本模型