"""总账 + 缓存累计曲线。"""
import argparse
import collections
import json
import math
from pathlib import Path

from ingest.canonical import CANON_VERSION
from lens_config import data_dir

DATA = data_dir()
OUT = DATA / "events" / f"v{CANON_VERSION}"

# $/MTok: (input, output). cache_read=0.1x input; write 5m=1.25x, 1h=2.0x
PRICE = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
DEFAULT_PRICE = (5.0, 25.0)


def cost_of(step):
    pin, pout = PRICE.get(step.get("model"), DEFAULT_PRICE)
    c1h, c5m = step.get("cache_1h", 0), step.get("cache_5m", 0)
    # 老版本语料没有拆分字段：全部按 5m 计（偏低估，显式标注）
    rest = max(0, step.get("cache_create", 0) - c1h - c5m)
    return (
        step.get("in_tok", 0) * pin
        + step.get("cache_read", 0) * pin * 0.1
        + (c5m + rest) * pin * 1.25
        + c1h * pin * 2.0
        + step.get("out_tok", 0) * pout
    ) / 1e6


def load():
    for f in sorted(OUT.glob("*/*.jsonl")):
        events = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        if events:
            yield f.parent.name, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args()

    ws = collections.defaultdict(lambda: collections.Counter())
    cat = collections.Counter()
    tools = collections.Counter()
    curves, alphas = [], []
    unknown_names = collections.Counter()

    for name, events in load():
        w = ws[name]
        w["sessions"] += 1
        cum, per_step = 0, []
        for e in events:
            ev = e["ev"]
            if ev == "step":
                w["steps"] += 1
                w["tool_calls"] += e["tool_calls"]
                if e["tool_calls"] == 0:
                    w["steps_no_tool"] += 1
                for k in ("in_tok", "cache_read", "cache_create", "out_tok"):
                    w[k] += e[k]
                w["cost_cents"] += cost_of(e) * 100
                inp = e["in_tok"] + e["cache_read"] + e["cache_create"]
                cum += inp
                per_step.append((inp, cum))
            elif ev == "tool.call":
                cat[e["category"]] += 1
                tools[e["name"]] += 1
                if e["category"] == "unknown":
                    unknown_names[e["name"]] += 1
            elif ev == "turn.user" and not e.get("is_meta"):
                w["user_turns"] += 1
        per_step = [(i, c) for i, c in per_step if c > 0]
        if len(per_step) >= 10:
            curves.append(per_step)
            n = len(per_step)
            xs = [math.log(i + 1) for i in range(n)]
            ys = [math.log(c) for _, c in per_step]
            mx, my = sum(xs) / n, sum(ys) / n
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                alphas.append(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den)

    rows = sorted(ws.items(), key=lambda kv: -kv[1]["cost_cents"])
    print(f"{'工作区':<52}{'会话':>5}{'step':>7}{'工具':>7}{'cache_read(M)':>14}{'成本$':>9}")
    tot = collections.Counter()
    for name, w in rows:
        tot.update(w)
        print(f"{name[:50]:<52}{w['sessions']:>5}{w['steps']:>7}{w['tool_calls']:>7}"
              f"{w['cache_read']/1e6:>14.1f}{w['cost_cents']/100:>9.2f}")
    print("-" * 94)
    print(f"{'合计':<52}{tot['sessions']:>5}{tot['steps']:>7}{tot['tool_calls']:>7}"
          f"{tot['cache_read']/1e6:>14.1f}{tot['cost_cents']/100:>9.2f}")

    ctotal = sum(cat.values())
    if ctotal:
        print("\n环节归类：", " · ".join(f"{k} {v}({v/ctotal:.1%})" for k, v in cat.most_common()))
        print("未命中 top:", dict(unknown_names.most_common(8)))
    print(f"\n每 step 平均并行宽度 {tot['tool_calls']/max(1,tot['steps']):.2f} 个工具"
          f" · 零工具 step 占比 {tot['steps_no_tool']/max(1,tot['steps']):.1%}")
    print(f"输入构成：cache_read {tot['cache_read']/1e6:.0f}M · cache_create "
          f"{tot['cache_create']/1e6:.0f}M · 未缓存 in {tot['in_tok']/1e6:.1f}M · 输出 {tot['out_tok']/1e6:.1f}M")
    if alphas:
        alphas.sort()
        q = lambda p: alphas[int(p * (len(alphas) - 1))]
        print(f"\n超线性指数 α（累计输入 ∝ n^α，n=step 序）：中位 {q(.5):.2f}"
              f" · 四分位 [{q(.25):.2f}, {q(.75):.2f}] · 样本 {len(alphas)} 会话（≥10 step）")
        print("  α≈1 表示每步输入恒定（线性累加）；α>1 表示历史越滚越大 → 减一轮的收益超线性")

    if a.plot:
        plot(curves)


def plot(curves):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = sorted(curves, key=len, reverse=True)[:120]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    for c in curves:
        ax1.plot([p[0] / 1e3 for p in c], alpha=.12, color="#c1440e", lw=.8)
        ax2.plot([p[1] / 1e6 for p in c], alpha=.12, color="#1f4e79", lw=.8)
    for ax, t, yl in ((ax1, "Per-step input (history resent each call)", "input tokens per step (K)"),
                      (ax2, "Cumulative input = what you pay for", "cumulative input tokens (M)")):
        ax.set_title(t, fontsize=11)
        ax.set_xlabel("step index (serial model calls)")
        ax.set_ylabel(yl)
        ax.grid(alpha=.25)
    fig.suptitle("Agent sessions: cost grows superlinearly with exploration depth", fontsize=12)
    fig.tight_layout()
    p = DATA / "superlinear.png"
    fig.savefig(p, dpi=130)
    print(f"\n图已存 {p}")


if __name__ == "__main__":
    main()
