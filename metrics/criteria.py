"""判据模块：从 canonical 事件计算探索判据（scope: session / task / agent-run）。

重算契约：
  - 只吃 canonical 事件（CANON_VERSION 一致），不吃任何宿主原始日志。
  - 同一份事件 + 同一 CRITERIA_VERSION → 逐字节相同的输出。
  - 判据只量「省不省」，不量「对不对」（质量归校验环节红绿灯）。

判据五件套（全部机械提取）：
  depth        串行深度 = step 数（成本单位；历史重发按它累乘）
  width        并行宽度 = 每 step 工具数均值（「一波找全」的可观测形态 = 宽度高深度低）
  files_read   检索类去重目标数（究竟找了多少文件/查询）
  ttfe_tok     tokens-to-first-edit：首次变更前烧掉的输入 tokens（探索的直接开销）
  rework       同目标返工次数：同一文件被变更 ≥2 次的额外次数

baseline：judge() 用全语料分布当分母——绝对值没有意义，位置才有。
"""
import argparse
import collections
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from ingest.canonical import CANON_VERSION  # noqa: E402
from lens_config import data_dir  # noqa: E402

CRITERIA_VERSION = 1
DATA = data_dir()
EVENTS = DATA / "events" / f"v{CANON_VERSION}"
BASELINE = DATA / f"baseline.c{CRITERIA_VERSION}.v{CANON_VERSION}.json"

METRIC_KEYS = ("depth", "width", "files_read", "ttfe_tok", "rework")


def split_tasks(events):
    """任务区间 = 相邻两条非 meta 用户消息之间（机械切分，误差率未校验，消费方自知）。"""
    bounds = [e["seq"] for e in events
              if e["ev"] == "turn.user" and not e.get("is_meta")]
    if not bounds:
        return [(0, float("inf"))]
    spans = [(b, bounds[i + 1] if i + 1 < len(bounds) else float("inf"))
             for i, b in enumerate(bounds)]
    return spans


def measure(events, lo=0, hi=float("inf")):
    """对 [lo, hi) 区间内的事件算判据五件套。scope=session 时传默认边界。"""
    steps = tool_calls = 0
    in_cum = 0
    ttfe = None
    read_targets = set()
    edit_counts = collections.Counter()
    for e in events:
        if not (lo <= e.get("seq", 0) < hi):
            continue
        ev = e["ev"]
        if ev == "step":
            steps += 1
            tool_calls += e.get("tool_calls", 0)
            in_cum += e.get("in_tok", 0) + e.get("cache_read", 0) + e.get("cache_create", 0)
        elif ev == "tool.call":
            cat = e.get("category")
            if cat == "retrieval" and e.get("target"):
                read_targets.add(e["target"])
            elif cat == "mutation":
                if ttfe is None:
                    ttfe = in_cum
                if e.get("target"):
                    edit_counts[e["target"]] += 1
        elif ev == "file.event" and e.get("path"):
            edit_counts[e["path"]] += 1
    rework = sum(c - 1 for c in edit_counts.values() if c > 1)
    return {
        "edited": ttfe is not None,  # False 时 ttfe_tok=全区间输入，是「纯阅读任务」不是探索开销
        "depth": steps,
        "width": round(tool_calls / steps, 3) if steps else 0.0,
        "files_read": len(read_targets),
        "ttfe_tok": ttfe if ttfe is not None else in_cum,
        "rework": rework,
    }


def iter_sessions():
    for f in sorted(EVENTS.glob("*/*.jsonl")):
        events = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        if events:
            yield f.parent.name, f.stem, events


def compute_all(scope):
    rows = []
    for ws, sid, events in iter_sessions():
        sess = events[0] if events[0]["ev"] == "session" else {}
        base = {"workspace": ws, "sid": sid, "scope": scope,
                "title": sess.get("ai_title"),
                "model_mix": None, "canon": CANON_VERSION, "criteria": CRITERIA_VERSION}
        models = collections.Counter(e.get("model") for e in events if e["ev"] == "step")
        base["model_mix"] = dict(models.most_common(3))
        if scope == "session":
            m = measure(events)
            if m["depth"]:
                rows.append({**base, **m})
        else:  # task
            for i, (lo, hi) in enumerate(split_tasks(events)):
                m = measure(events, lo, hi)
                if m["depth"]:
                    rows.append({**base, "task_idx": i, **m})
    return rows


def build_baseline(rows):
    dist = {k: sorted(r[k] for r in rows) for k in METRIC_KEYS}
    def pct(vals, ps=(10, 25, 50, 75, 90)):
        return {f"p{p}": vals[min(len(vals) - 1, int(p / 100 * len(vals)))] for p in ps} if vals else {}
    return {"n": len(rows), "scope": rows[0]["scope"] if rows else None,
            "percentiles": {k: pct(v) for k, v in dist.items()},
            "_dist": {k: v for k, v in dist.items()}}


def judge(m, baseline):
    """给一次运行的判据打位置分：每项返回它在语料分布里的百分位。"""
    out = {}
    for k in METRIC_KEYS:
        vals = baseline["_dist"][k]
        if not vals:
            out[k] = None
            continue
        import bisect
        out[k] = round(bisect.bisect_left(vals, m[k]) / len(vals) * 100, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["session", "task"], default="task")
    ap.add_argument("--rebuild-baseline", action="store_true")
    a = ap.parse_args()
    rows = compute_all(a.scope)
    out = DATA / f"metrics.{a.scope}.c{CRITERIA_VERSION}.v{CANON_VERSION}.jsonl"
    with open(out, "w", encoding="utf-8") as w:
        for r in rows:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} 条 {a.scope} 级判据 → {out.name}")
    bl = build_baseline(rows)
    BASELINE.write_text(json.dumps(bl, ensure_ascii=False), encoding="utf-8")
    p = bl["percentiles"]
    print(f"{'指标':<12}" + "".join(f"{q:>8}" for q in ("p10", "p25", "p50", "p75", "p90")))
    for k in METRIC_KEYS:
        print(f"{k:<12}" + "".join(f"{p[k].get(q, ''):>8}" for q in ("p10", "p25", "p50", "p75", "p90")))


if __name__ == "__main__":
    main()
