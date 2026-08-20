"""适配器 #2：dsh session log → canonical 事件（stub）。

存在的意义是**结构证明**：core 层没焊死在 Claude 上。
dsh 侧的映射（对 dsh 日志机制核实过的事实）：
  event `step`        → core `step`（dsh 的 step 定义就是「一次模型调用 + 它请求的多个工具执行」，天然对齐）
  event `tool/call`   → core `tool.call`（.name 直接喂 classify()）
  event `tool/result` → core `tool.result`（.meta 带 dsh-tool-fs 文件 diff → 可直接产 core `file.event`，比 Claude 侧的 file-history 轨更硬）
  event `todo/write`  → 无 core 对应；是 **agent-run scope 判据的原料**（拆分快照，log-only 不进模型可见面）
  每 agent 独立 session log → core `agent.run` 天然成立，**不需要像 Claude 侧那样靠 isSidechain 降级推断**

未实装：手头 dsh 语料仅 1 天量级，实装等语料够。
"""
from .canonical import CANON_VERSION  # noqa: F401

HARNESS = "dsh"


def parse(path):
    raise NotImplementedError("dsh 语料量不足；实装前先攒够样本")
