# Canonical 事件词汇（规范件）

跨 harness 的中立事件模型。**分析器只吃本层，不许直连任何宿主的原始日志。**
这条约束是「判据模块」路线的地基：判据要能在 dsh 日志上重算，就不能依赖 Claude 的字段名。

## 分层

- **core**：两个 harness 都保证能提供的。core 分析器跨 harness 复用。
- **ext.\<harness\>.\***：宿主特有。ext 分析器只在有该字段的语料上跑，**必须显式报告覆盖率**。

## core 事件

所有事件共有：`ev`（类型）、`sid`（会话 id）、`ts`（ISO 时间，可空）、`seq`（文件内序号）。

| ev | 字段 | 说明 |
|:--|:--|:--|
| `session` | `harness`, `harness_version`, `workspace`, `git_branch` | 每会话一条，取首次出现值 |
| `turn.user` | `text_len`, `text_hash`, `attachments`, `is_meta` | **不存原文**。用户消息＝任务边界的机械信号 |
| `step` | `model`, `effort`, `in_tok`, `cache_read`, `cache_create`(+`cache_1h`/`cache_5m` 拆分), `out_tok`, `tool_calls` | **成本单位**＝一次模型调用。`tool_calls`＝本 step 请求的工具数（并行宽度） |
| `tool.call` | `step_seq`, `name`, `category`, `target`, `raw` | `target`＝文件路径或命令首 token；`raw`＝命令原文**截断 200 字符**，仅供归类器复查 |
| `tool.result` | `call_seq`, `ok`, `size` | 结果体积用于估算被重发的历史增量 |
| `file.event` | `path`, `kind` | 来自宿主的文件变更轨（**若有**），比从 tool.call 反推硬 |
| `agent.run` | `agent_type`, `parent` | 子 agent 边界。scope=agent-run 的判据依赖它 |

### 宿主能力差异（写死在这里，免得每次重新发现）

| 能力 | Claude Code | Pi |
|:--|:--|:--|
| 一次模型调用 ↔ 记录数 | **1 : N**（最多 4 条，每条重复同一份 usage）→ 必须按 requestId 聚合 | **1 : 1**（全部 content block 同条、一份 usage）→ 不需要聚合 |
| 独立文件变更轨 | 有（`file-history-delta`） | **无** → 只能从 tool.call 参数反推 |
| 子 agent | 有（一个子 agent 一份日志） | **刻意没有** → `agent.run` 不适用 |
| 会话结构 | 线性追加 | **树**（`id`/`parentId`，可原地分叉）→ 适配器只取活动分支，放弃支计入 `stats.off_path` |
| 成本 | 需外挂价目表 | **日志自带** `usage.cost` |

⇒ **`file.event` 只在宿主有独立变更轨时发。** Pi 侧不发——若两边都发，
`criteria.measure()` 会把同一次编辑在 `edit_counts` 里数两遍（tool.call 的 mutation 与 file.event 各一次），
`rework` 直接翻倍。代价：Pi 上 bash 驱动的变更（`sed -i` / `>`）拿不到文件路径，**`rework` 是下界**。

## category（环节归类，纯机械零模型算力）

| category | 含义 | 判定 |
|:--|:--|:--|
| `retrieval` | 检索 | 工具名表 + bash 只读命令正则 |
| `mutation` | 变更 | 工具名表 + bash 写命令正则 |
| `verification` | 校验 | bash test/build/lint/check 正则为主 |
| `execution` | 执行 | 起服务 / 跑脚本 / 调 CLI —— **既不是检索也不是变更**（v5 补，实测占 ~10%） |
| `coordination` | 协调 | todo / 子 agent / 计划 / 问用户 |
| `other` | 其他 | 命中但不属五类 |
| `unknown` | **未命中** | **占比必须显式报告，不许静默归入 other** |

### 壳命令归类的两条已知遮蔽（都吃过血）

1. **前缀遮蔽**：大量命令以 `cd xxx &&` 或环境变量赋值开头，正则先撞上 `cd` 就把整条判成检索。
   ⇒ 匹配前必须先剥前缀（`_PREFIX`）。
2. **语句起点遮蔽**（v6 补）：`(^|[;&|])` 不含换行，导致**多行脚本里第 2 行起的命令完全不可见**；
   `for …; do cp …; done` 里动词前多一个 `do` 也匹配不上。
   ⇒ 语句起点扩为 `(^|[;&|\n]\s*|\b(do|then|else)\s+)`。
   **代价（诚实声明）**：多行脚本常常同时含变更与执行，单标签按固定优先级
   （校验 > 执行 > 变更 > 检索）取一个 —— 换行可见之后这种有损更常见。

### `target` 的口径边界（**判据消费方必读**）

`target_of()` 对壳命令返回**命令首 token**，不是文件路径。后果：

- N 次不同的 `grep` 会坍缩成**同一个** target（实测某 Pi 会话 87 次 grep → 1 个目标）；
- `cd` 开头的命令 target 就是 `cd`。

⇒ **`files_read`（检索去重目标数）在壳命令占比高的宿主上严重低估**，
Pi 尤其严重（默认不开 grep/find/ls 工具，检索全挤到 bash）。
`ledger.py` 已用 `FILE_TOOLS` 白名单规避；**`criteria.py` 的 `files_read` 没有这道规避**，
消费该指标时必须知道这条，或另算路径级补充指标。

> ⚠ 实证：每个**新的 target 消费点**都会重新踩这个坑（`ledger` → `harvest` 文件 TOP →
> `reader` 返工热点，三次）。新代码凡把 `target` 当文件路径用，先问一句：
> 「壳命令的 target 进来会怎样？」——答案永远是套 `MUTATION`/`RETRIEVAL` 工具名白名单。

## 隐私约束（写进 schema 而不只是 README）

- 任何 core 字段**不得**包含自然语言原文：用户/助手文本仅存 `text_len` + `text_hash`。
- `tool.call.raw` 是唯一的原文通道，截断 200 字符，仅用于归类器复查，**不得进入任何对外产物**。
- 文件路径保留（对照分析需要工作区归属），对外发布时按工作区聚合后剥离。

## 适配器清单

| # | 宿主 | 文件 | 状态 |
|:--|:--|:--|:--|
| 1 | Claude Code | `ingest/adapter_claude.py` | 实装（私有无契约格式，抓取） |
| 2 | dsh | `ingest/adapter_dsh.py` | stub（语料量不足） |
| 3 | Pi | `ingest/adapter_pi.py` | 实装（**有文档契约**：`docs/session-format.md`） |

宿主特有事件用 `ext.<harness>.*` 命名，core 分析器可以整体忽略。
Pi 侧现有：`ext.pi.compaction`（带 `tokens_before`，标记 α 越界）、`ext.pi.bashExecution`（用户敲的 `!` 命令，
**不是模型行为**，不进 step/tool.call）、`ext.pi.model_change` / `ext.pi.thinking_change`（用来核对实验参数钉住没）、
`ext.pi.branch_summary` / `ext.pi.label` / `ext.pi.custom`。

## 版本

`CANON_VERSION = 6`。事件字段增删即 +1；`data/events/` 里带版本，版本不符触发重扫。

| 版本 | 改了什么 |
|:--|:--|
| 2 | 本规范件首版记录的状态 |
| 3–4 | （见 git / manifest；本文件当时未同步） |
| 5 | 补 `execution` 环节类；壳命令前缀剥离 |
| 6 | Pi 工具名（小写 read/bash/edit/write/grep/find/ls）进分类集合；补词表（npm 的 check / typecheck、`npm run publish` 与 `npm run version:*`、biome、code、date）；**语句起点补全**（换行 + do/then/else）；新增 `ext.pi.*` 事件族 |

**增量纪律**：改分类器必须证明「只把 `unknown` 变成有类，不改判已归类的调用」，
证明方式 = 拿改前快照与改后代码各跑一遍全量语料、列出所有类别迁移。
v6 的实测（私有语料，24,456 次调用）：工具名批与补词表批**零改判**；语句起点批改判 98 次（1.28 %），逐条核过。
