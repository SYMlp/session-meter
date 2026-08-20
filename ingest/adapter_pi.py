"""适配器 #3：Pi（earendil-works/pi）session JSONL → canonical 事件。

与前两个适配器的定位差别：
  - Claude 侧是**私有无契约**格式，靠抓取；
  - dsh 侧是自家插件宿主；
  - **Pi 侧有文档契约**（`packages/coding-agent/docs/session-format.md`）+ TS 类型可查。

三条 Pi 特有的机制事实，直接决定本文件怎么写：

1. **一次模型调用 = 一条 assistant 记录**，全部 content block（text / thinking / toolCall × N）
   在同一条里，`usage` 只出现一次。⇒ `step` 无需像 Claude 侧那样按 requestId 聚合，
   **文章坑一在 Pi 上结构性不存在**。（Pi 自己的事件流术语 `turn_start`/`turn_end` 与 step 同义。）

2. **session 是树**（v2/v3 的 `id`/`parentId`，支持原地分叉）。分析只取**当前活动分支**
   （从文件末条即 leaf 走到 root），被放弃的分支条数记进 `stats["off_path"]` 显式报告。
   v1 语料没有 id/parentId，退化为文件顺序线性读。

3. **Pi 没有独立的文件变更轨**（Claude 侧有 `file-history-delta`）。文件事件只能从 toolCall
   参数反推，而这已经由 `tool.call` 的 mutation 类覆盖。⇒ **本适配器不发 `file.event`**，
   避免同一次编辑在 criteria 的 `edit_counts` 里被数两遍（tool.call 与 file.event 各一次）。
   代价（诚实边界）：`sed -i` / `>` 重定向这类 bash 驱动的变更拿不到文件路径，
   所以 Pi 上的 `rework` 是**下界**，不是全量。

隐私约束同 schema：core 字段不存自然语言原文，用户文本只存长度 + 哈希，
bash 命令原文截断 200 字符仅供归类器复查。
"""
import hashlib
import json

from .canonical import CANON_VERSION, classify, target_of

HARNESS = "pi"

# 不是模型工具调用、但会进上下文的记录；单列成 ext 事件，绝不当 step / tool.call
_EXT_ROLES = {"bashExecution", "branchSummary", "compactionSummary"}


def _text_stats(content):
    """→ (总文本长度, 哈希, 非文本块数)。不返回原文。"""
    if isinstance(content, str):
        return len(content), hashlib.sha1(content.encode("utf-8", "ignore")).hexdigest()[:12], 0
    if not isinstance(content, list):
        return 0, "", 0
    txt = "".join(b.get("text", "") for b in content
                  if isinstance(b, dict) and b.get("type") == "text")
    attach = sum(1 for b in content if isinstance(b, dict) and b.get("type") != "text")
    return len(txt), hashlib.sha1(txt.encode("utf-8", "ignore")).hexdigest()[:12], attach


def _result_size(msg):
    n = 0
    for b in (msg.get("content") or []):
        if isinstance(b, dict):
            t = b.get("text")
            n += len(t) if isinstance(t, str) else len(b.get("data") or "")
    return n


def _active_branch(entries, stats):
    """树形 session 取当前活动分支：leaf（文件末条）→ root，再翻回正序。

    v1 语料无 id/parentId → 原样返回文件顺序。
    """
    by_id = {e["id"]: e for e in entries if e.get("id")}
    if not by_id:
        return entries
    leaf = None
    for e in reversed(entries):
        if e.get("id"):
            leaf = e
            break
    path, seen = [], set()
    cur = leaf
    while cur is not None:
        if cur["id"] in seen:  # 环（不该出现）——断开，别死循环
            break
        seen.add(cur["id"])
        path.append(cur)
        pid = cur.get("parentId")
        cur = by_id.get(pid) if pid else None
    path.reverse()
    on_path = {e["id"] for e in path}
    # 无 id 的条目（如 v1 尾部混入）保序保留在前
    tail = [e for e in entries if not e.get("id")]
    stats["off_path"] = sum(1 for e in entries if e.get("id") and e["id"] not in on_path)
    return tail + path


def parse(path):
    """→ (events: list[dict], stats: dict)

    stats 口径：
      lines     读到的非空行数
      bad_json  解析失败行数
      degraded  assistant 记录缺 usage 的条数（不编值，字段留 0 并计数）
      sidechain 恒 0：Pi 刻意不做子 agent
      off_path  被放弃分支上的条目数（活动分支之外，不参与分析）
    """
    stats = {"lines": 0, "bad_json": 0, "degraded": 0, "sidechain": 0, "off_path": 0}
    raw_entries, header = [], None

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                r = json.loads(line)
            except Exception:
                stats["bad_json"] += 1
                continue
            if r.get("type") == "session" and header is None:
                header = r
                continue
            raw_entries.append(r)

    header = header or {}
    sess = {
        "ev": "session", "harness": HARNESS, "canon": CANON_VERSION,
        "sid": header.get("id"),
        "workspace": header.get("cwd"),
        "git_branch": None,          # Pi 的 header 不记分支
        "harness_version": None,     # Pi 的 header 不记 CLI 版本（区别于 Claude 的 version 字段）
        "sess_fmt": header.get("version", 1),   # session 文件格式版本（1 线性 / 2,3 树）
        "provider0": header.get("provider"),    # 开场 provider/model/thinking，实验里用来核对钉死没钉住
        "model0": header.get("modelId"),
        "thinking0": header.get("thinkingLevel"),
        "parent_session": header.get("parentSession"),
        "ai_title": None,            # Pi 无 AI 标题；显示名走 session_info
        "ts_first": header.get("timestamp"), "ts_last": header.get("timestamp"),
        "agent_name": None, "agent_type": None, "is_subagent": False,
    }

    entries = _active_branch(raw_entries, stats)

    events = []
    call_seq_by_id = {}
    seq = 0
    thinking = header.get("thinkingLevel")   # thinkingLevel 只在 header 与 change 条目里，需自己跟踪
    last_step_seq = None

    for r in entries:
        seq += 1
        t = r.get("type")
        ts = r.get("timestamp")
        if ts:
            sess["ts_first"] = sess["ts_first"] or ts
            sess["ts_last"] = ts

        if t == "thinking_level_change":
            thinking = r.get("thinkingLevel")
            events.append({"ev": "ext.pi.thinking_change", "seq": seq, "ts": ts,
                           "side": False, "level": thinking})
            continue

        if t == "model_change":
            events.append({"ev": "ext.pi.model_change", "seq": seq, "ts": ts, "side": False,
                           "provider": r.get("provider"), "model": r.get("modelId")})
            continue

        if t == "compaction":
            # 压缩 = 换 regime（文章：α 只描述触顶之前）。显式留痕，便于事后判某 run 是否越界。
            events.append({"ev": "ext.pi.compaction", "seq": seq, "ts": ts, "side": False,
                           "tokens_before": r.get("tokensBefore"),
                           "from_hook": bool(r.get("fromHook")),
                           "retained_tail": len(r.get("retainedTail") or []),
                           "sum_len": len(r.get("summary") or "")})
            continue

        if t == "session_info":
            sess["ai_title"] = r.get("name")
            continue

        if t in ("branch_summary", "label", "custom"):
            events.append({"ev": f"ext.pi.{t}", "seq": seq, "ts": ts, "side": False,
                           "custom_type": r.get("customType")})
            continue

        if t == "custom_message":
            # 扩展注入的消息：**进模型可见面**，但不是人打的 → is_meta=True，不当任务边界
            n, h, att = _text_stats(r.get("content"))
            events.append({"ev": "turn.user", "seq": seq, "ts": ts, "side": False,
                           "text_len": n, "text_hash": h, "attachments": att,
                           "is_meta": True, "src": f"ext:{r.get('customType')}", "mode": None})
            continue

        if t != "message":
            continue

        msg = r.get("message") or {}
        role = msg.get("role")

        if role == "user":
            n, h, att = _text_stats(msg.get("content"))
            events.append({"ev": "turn.user", "seq": seq, "ts": ts, "side": False,
                           "text_len": n, "text_hash": h, "attachments": att,
                           "is_meta": False, "src": None, "mode": None})

        elif role == "assistant":
            u = msg.get("usage") or {}
            if not u:
                stats["degraded"] += 1
            blocks = [b for b in (msg.get("content") or [])
                      if isinstance(b, dict) and b.get("type") == "toolCall"]
            cost = u.get("cost") or {}
            step = {
                "ev": "step", "seq": seq, "ts": ts, "side": False,
                "model": msg.get("model"), "effort": thinking,
                "in_tok": u.get("input", 0),
                "cache_read": u.get("cacheRead", 0),
                "cache_create": u.get("cacheWrite", 0),
                "out_tok": u.get("output", 0),
                "cache_1h": 0, "cache_5m": 0,   # Pi 不拆缓存 TTL
                "iters": 1,
                "records": 1,                   # Pi 的结构事实：一次调用恒 1 条记录
                "tool_calls": len(blocks),
                # ext：Pi 自带钱与停因，成本核算与「做没做完」判定都直接用
                "provider": msg.get("provider"), "api": msg.get("api"),
                "cost_usd": cost.get("total"),
                "stop": msg.get("stopReason"),
                "err": msg.get("errorMessage"),
                "blocks": {k: sum(1 for b in (msg.get("content") or [])
                                  if isinstance(b, dict) and b.get("type") == k)
                           for k in ("text", "thinking", "toolCall")},
            }
            events.append(step)
            last_step_seq = seq
            for b in blocks:
                seq += 1
                name = b.get("name") or ""
                inp = b.get("arguments") or {}
                raw = inp.get("command") if isinstance(inp, dict) else None
                call_seq_by_id[b.get("id")] = seq
                events.append({"ev": "tool.call", "seq": seq, "ts": ts, "side": False,
                               "step_seq": step["seq"], "name": name,
                               "category": classify(name, raw),
                               "target": target_of(name, inp),
                               "raw": (raw or "")[:200]})

        elif role == "toolResult":
            events.append({"ev": "tool.result", "seq": seq, "ts": ts, "side": False,
                           "call_seq": call_seq_by_id.get(msg.get("toolCallId")),
                           "ok": not msg.get("isError"),
                           "size": _result_size(msg),
                           "name": msg.get("toolName"),
                           "nested_usage": bool(msg.get("usage"))})

        elif role in _EXT_ROLES:
            # bashExecution = 用户自己敲的 ! 命令，不是模型行为：不进 step / tool.call，
            # 但它进上下文（除 !! 前缀），所以单列可见。
            cmd = msg.get("command") if role == "bashExecution" else None
            events.append({"ev": f"ext.pi.{role}", "seq": seq, "ts": ts, "side": False,
                           "step_seq": last_step_seq,
                           "raw": (cmd or "")[:200],
                           "category": classify("bash", cmd) if cmd else None,
                           "excluded": bool(msg.get("excludeFromContext")),
                           "exit": msg.get("exitCode")})

    events.insert(0, sess)
    return events, stats
