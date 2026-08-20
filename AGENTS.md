# AGENTS.md

session-lens（占位名）——跨 harness 的 agent 会话日志计量内核。本文件是项目规约唯一真相源；`CLAUDE.md` 只做 `@AGENTS.md` 薄导入。

## 定位

把任意 harness（Claude Code / dsh / Pi / …）的 agent 会话日志解析成**中立事件层**，在其上机械提取探索判据与每日收割单。分析器只吃中立事件，不许直连宿主原始日志——这是全部可迁移性的来源。

- 规范件：`schema/canonical.md`（中立事件词汇 + 环节归类 + 宿主能力差异表）
- 入口脚本：`scan.py`（raw → 事件）、`report.py`（总账）、`ledger.py`（会话账本）、`metrics/criteria.py`（判据五件套）

## 红线（每次会话必守）

1. **本仓不得出现任何真实日志内容、真实路径、真实工作区名、人名**。测试与示例一律用自造 fixture（`tests/fixtures/`）。
2. **中立事件不存自然语言原文**：用户/助手文本只存长度+哈希；`tool.call.raw` 截断 200 字符且不得进任何对外产物。
3. 私有配置（日志根路径、数据目录、输出路径）只住 `config.yaml`（已 gitignore）；仓里只留 `config.example.yaml`。
4. 新功能自检一问：**「换一个 harness 当执行底座，这功能还成立吗？」**——不成立的写在了宿主私有字段上，退回中立层重写。
5. 改分类器必须过增量纪律：证明「只把 unknown 变成有类，不改判已归类的调用」（改前快照 vs 改后代码各跑全量语料，列类别迁移明细）。

## 工程口径

- Python 全 stdlib（matplotlib 仅 `report.py --plot` 可选）；conda env `session-lens`。
- 判据只量「省不省」，不量「对不对」；`unknown` 占比必须显式报告。
- 不做 backward-compat；`CANON_VERSION` 变更即重扫。
