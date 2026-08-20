# session-lens

> 占位仓名，待定。

跨 harness 的 **agent 会话日志计量内核**：把 Claude Code / dsh / Pi 等宿主的会话日志解析成中立事件层，在其上机械提取探索判据——不靠模型算力，不碰对话原文。

## 它回答什么问题

- 这个 agent 一场会话到底走了几步、每步并行叫了几个工具、找了多少文件才动手改？
- 成本花在哪：历史重发随探索深度**超线性**增长（实测 α 中位 ≈1.28——探索轮次翻倍、总输入 ≈2.4 倍），"少走一轮"的收益远大于"少调一次工具"。
- 各环节占比：检索 / 变更 / 校验 / 执行 / 协调，纯词表+正则归类，`unknown` 占比显式报告。

## 设计不变量

1. **分析器只吃中立事件层**（`schema/canonical.md`），不许直连宿主原始日志。换 harness = 写一个适配器，全部分析复用。可迁移性靠构造，不靠指望。
2. **不存自然语言原文**：用户/助手文本只存长度+哈希；bash 命令截断 200 字符仅供归类器复查。日志是使用者的全部生活，隐私约束写进 schema 而不只是 README。
3. **判据只量「省不省」，不量「对不对」**：质量归校验环节的红绿灯，两者合起来才是完整裁判。
4. **一次模型调用 = 一个 `step`**：Claude Code 转写把一次调用写成最多 4 条记录（每条重复同一份 usage），适配器按 requestId 聚合——按记录求和会把成本虚高约 1.7 倍。自查判据：若"每步工具数"分布几乎全是 0 和 1，你的步定义错了。

## 结构

```
schema/canonical.md        中立事件词汇（规范件：core/ext 分层、环节归类、宿主能力差异表）
ingest/canonical.py        事件定义 + 环节归类表（CANON_VERSION）
ingest/adapter_claude.py   适配器 #1：Claude Code 转写 jsonl（私有无契约格式，含 subagents 层）
ingest/adapter_dsh.py      适配器 #2：dsh session log（stub，结构证明 core 没焊死）
ingest/adapter_pi.py       适配器 #3：Pi session jsonl（有文档契约；树形分支取活动路径）
metrics/criteria.py        判据五件套：depth / width / files_read / ttfe_tok / rework
scan.py                    raw → canonical events（增量，manifest 去重）
report.py                  总账 + 环节占比 + 超线性指数 α
ledger.py                  会话账本：每 session 改/读/跑了什么
lens_config.py             配置解析（私有路径全部外置）
```

## 跑法

```bash
cp config.example.yaml config.yaml   # 改成你的日志根目录（config.yaml 已 gitignore）
python scan.py --all                 # 增量扫描全部工作区（含 subagents 层）
python report.py                     # 总账 + α
python metrics/criteria.py --scope task
python ledger.py <workspace-slug> --days 7
```

全 stdlib（`report.py --plot` 需 matplotlib）。冒烟测试：`python tests/test_smoke.py`（用自造 fixture，不需要任何真实日志）。

## 诚实边界

- Claude Code 转写是**私有无契约格式**（实测语料横跨 21 个 CLI 版本），这是抓取不是合同；字段缺失一律降级计数，不抛异常不编值。
- 任务切分（相邻用户消息之间）是机械近似，误差率未校验。
- `files_read` 对壳命令只取命令首 token，壳命令占比高的宿主（Pi 尤甚）会严重低估——见 `schema/canonical.md` 的 target 口径边界。
- 判据的绝对值没有意义，位置才有（`judge()` 用你自己语料的分布当分母）。

## License

MIT
