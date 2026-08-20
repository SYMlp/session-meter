"""适配器 #1：Claude Code 转写 jsonl → canonical 事件。

诚实边界：Claude 转写是私有无契约格式（本机语料横跨 21 个 CLI 版本），
字段缺失一律降级为 None 并计入 degraded 计数，不抛异常、不静默编值。
"""
import hashlib
import json

from .canonical import CANON_VERSION, classify, target_of

HARNESS = "claude-code"


def _text_stats(content):
    if isinstance(content, str):
        t = content
    elif isinstance(content, list):
        t = "".join(b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text")
    else:
        return 0, ""
    return len(t), hashlib.sha1(t.encode("utf-8", "ignore")).hexdigest()[:12]


def _result_size(msg):
    n = 0
    for b in (msg.get("content") or []):
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            n += len(c) if isinstance(c, str) else len(json.dumps(c, ensure_ascii=False)) if c else 0
    return n


def parse(path):
    """→ (events: list[dict], stats: dict)"""
    events, stats = [], {"lines": 0, "bad_json": 0, "degraded": 0, "sidechain": 0}
    sess = {"ev": "session", "harness": HARNESS, "canon": CANON_VERSION,
            "sid": None, "workspace": None, "git_branch": None,
            "harness_version": None, "ai_title": None, "ts_first": None, "ts_last": None,
            "agent_name": None, "agent_type": None, "is_subagent": None}
    call_seq_by_id = {}
    seq = 0
    last_step_seq = None
    cur_key, cur_step = None, None

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
            seq += 1
            t = r.get("type")
            ts = r.get("timestamp")
            if ts:
                sess["ts_first"] = sess["ts_first"] or ts
                sess["ts_last"] = ts
            sess["sid"] = sess["sid"] or r.get("sessionId")
            sess["workspace"] = sess["workspace"] or r.get("cwd")
            sess["git_branch"] = sess["git_branch"] or r.get("gitBranch")
            if r.get("version"):
                sess["harness_version"] = r["version"]
            side = bool(r.get("isSidechain"))
            if side:
                stats["sidechain"] += 1

            if t == "ai-title":
                sess["ai_title"] = r.get("aiTitle")

            elif t == "agent-name":
                sess["agent_name"] = r.get("agentName") or r.get("name")

            elif t == "agent-setting":
                sess["agent_type"] = (r.get("agentType") or r.get("subagentType")
                                      or r.get("setting") or None)

            elif t == "user" and "toolUseResult" not in r:
                msg = r.get("message") or {}
                n, h = _text_stats(msg.get("content"))
                events.append({"ev": "turn.user", "seq": seq, "ts": ts, "side": side,
                               "text_len": n, "text_hash": h,
                               "is_meta": bool(r.get("isMeta")),
                               "src": r.get("promptSource"),
                               "mode": r.get("permissionMode")})

            elif t == "user":
                msg = r.get("message") or {}
                for b in (msg.get("content") or []):
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    events.append({"ev": "tool.result", "seq": seq, "ts": ts, "side": side,
                                   "call_seq": call_seq_by_id.get(b.get("tool_use_id")),
                                   "ok": not b.get("is_error"),
                                   "size": _result_size(msg)})

            elif t == "assistant":
                msg = r.get("message") or {}
                # 一次模型调用 = 一个 requestId，可能被写成多条 assistant 记录，
                # 每条都重复同一份 usage。按 requestId 聚合，否则成本与并行宽度全错。
                key = r.get("requestId") or msg.get("id") or f"_seq{seq}"
                u = msg.get("usage") or {}
                if not u:
                    stats["degraded"] += 1
                its = u.get("iterations") or []
                blocks = [b for b in (msg.get("content") or [])
                          if isinstance(b, dict) and b.get("type") == "tool_use"]
                if key != cur_key:
                    cur_key = key
                    cur_step = {"ev": "step", "seq": seq, "ts": ts, "side": side,
                                "model": msg.get("model"), "effort": r.get("effort"),
                                "in_tok": u.get("input_tokens", 0),
                                "cache_read": u.get("cache_read_input_tokens", 0),
                                "cache_create": u.get("cache_creation_input_tokens", 0),
                                "out_tok": u.get("output_tokens", 0),
                                "cache_1h": (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0),
                                "cache_5m": (u.get("cache_creation") or {}).get("ephemeral_5m_input_tokens", 0),
                                "iters": max(1, len(its)),
                                "records": 0, "tool_calls": 0}
                    events.append(cur_step)
                cur_step["records"] += 1
                cur_step["tool_calls"] += len(blocks)
                step_seq = cur_step["seq"]
                last_step_seq = step_seq
                for b in blocks:
                    seq += 1
                    name = b.get("name") or ""
                    inp = b.get("input") or {}
                    raw = inp.get("command") if isinstance(inp, dict) else None
                    call_seq_by_id[b.get("id")] = seq
                    events.append({"ev": "tool.call", "seq": seq, "ts": ts, "side": side,
                                   "step_seq": step_seq, "name": name,
                                   "category": classify(name, raw),
                                   "target": target_of(name, inp),
                                   "raw": (raw or "")[:200]})

            elif t == "file-history-delta":
                events.append({"ev": "file.event", "seq": seq, "ts": r.get("timestamp"),
                               "side": False, "step_seq": last_step_seq,
                               "path": r.get("trackingPath"), "kind": "write"})

    sess["is_subagent"] = stats["sidechain"] > 0
    events.insert(0, sess)
    return events, stats
