"""会话账本：每个 session 具体调用了什么、动了哪些文件（述职投影毛坯）。

用法:
  python ledger.py <workspace-slug> [--days N] [--min-steps 3] [--hotspots] [--full]

诚实边界：文件名只取真实文件工具（Read/Write/Edit/Grep 等）的 file_path 参数；
shell 命令的目标是不可靠的（首 token 常是 cd/git），单独归入「跑了什么」。
"""
import argparse
import collections
import datetime
import json

from ingest.canonical import CANON_VERSION, MUTATION, RETRIEVAL
from lens_config import data_dir
from report import cost_of

DATA = data_dir()
EVENTS = DATA / "events" / f"v{CANON_VERSION}"
FILE_TOOLS = MUTATION | RETRIEVAL  # 只有这些工具的 target 是可信文件路径


def basename(t):
    t = (t or "").replace("\\", "/").rstrip("/")
    return t.split("/")[-1][:44] if "/" in t else t[:44]


def session_digest(events):
    sess = events[0] if events and events[0]["ev"] == "session" else {}
    d = {"cat": collections.Counter(), "tools": collections.Counter(),
         "edits": collections.Counter(), "reads": collections.Counter(),
         "runs": collections.Counter(), "steps": 0, "turns": 0, "cost": 0.0,
         "date": (sess.get("ts_first") or "")[:10],
         "title": sess.get("ai_title") or "", "branch": sess.get("git_branch")}
    for e in events:
        ev = e["ev"]
        if ev == "step":
            d["steps"] += 1
            d["cost"] += cost_of(e)
        elif ev == "turn.user" and not e.get("is_meta"):
            d["turns"] += 1
        elif ev == "tool.call":
            name, catg = e["name"], e["category"]
            d["cat"][catg] += 1
            d["tools"][name] += 1
            if name in FILE_TOOLS:
                b = basename(e.get("target"))
                if b:
                    (d["edits"] if catg == "mutation" else d["reads"])[b] += 1
            elif catg == "execution":
                raw = (e.get("raw") or "").strip()
                # 取命令里第一个像可执行名的词（跳过 cd/env 前缀）
                for w in raw.replace("&&", " ").replace(";", " ").split():
                    w = w.strip("\"'&|()")
                    if w and not w.startswith(("$", "-", "[")) and w.lower() not in (
                            "cd", "set-location", "pushd", "export", "then", "do"):
                        d["runs"][basename(w)] += 1
                        break
        elif ev == "file.event" and e.get("path"):
            d["edits"][basename(e["path"])] += 1
    return d


def load(workspace):
    for f in sorted((EVENTS / workspace).glob("*.jsonl")):
        events = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        if events:
            yield session_digest(events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--min-steps", type=int, default=3)
    ap.add_argument("--hotspots", action="store_true", help="只出文件热点榜与工具分布")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()

    all_d = sorted(load(a.workspace), key=lambda d: d["date"], reverse=True)
    if a.days:
        cutoff = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
        all_d = [d for d in all_d if d["date"] >= cutoff]
    keep = [d for d in all_d if d["steps"] >= a.min_steps]
    dropped = len(all_d) - len(keep)

    agg = {k: collections.Counter() for k in ("edits", "reads", "runs", "tools", "cat")}
    for d in keep:
        for k in agg:
            agg[k].update(d[k])
    total = sum(d["cost"] for d in keep)

    if not a.hotspots:
        lines = []
        for d in keep:
            catline = " ".join(f"{k[:3]}{v}" for k, v in d["cat"].most_common(5))
            lines.append(f"{d['date']} ${d['cost']:>6.1f} {d['turns']:>3}轮 {d['steps']:>4}步  {catline}")
            lines.append(f"    《{(d['title'] or '(无标题)')[:56]}》")
            for label, key in (("改", "edits"), ("读", "reads"), ("跑", "runs")):
                top = " · ".join(f"{k}×{v}" if v > 1 else k for k, v in d[key].most_common(3))
                if top:
                    lines.append(f"      {label}: {top}")
        print("\n".join(lines))
        if a.full:
            out = DATA / f"ledger.{a.workspace}.md"
            out.write_text("\n".join(lines), encoding="utf-8")
            print(f"\n完整账本 → {out}")

    print(f"\n{'='*70}\n{a.workspace}  最近 {a.days or '全部'} 天"
          f"  会话 {len(keep)} 个（过滤掉 {dropped} 个 <{a.min_steps} 步的空会话）  合计 ${total:.0f}")
    print(f"\n■ 改得最多的文件（跨会话）")
    for k, v in agg["edits"].most_common(12):
        print(f"    {v:>4}×  {k}")
    print(f"\n■ 读得最多的目标")
    for k, v in agg["reads"].most_common(8):
        print(f"    {v:>4}×  {k}")
    print(f"\n■ 跑得最多的东西")
    for k, v in agg["runs"].most_common(8):
        print(f"    {v:>4}×  {k}")
    print(f"\n■ 工具调用分布（这就是这个工作区的实际工作集）")
    tt = sum(agg["tools"].values())
    for k, v in agg["tools"].most_common(14):
        print(f"    {v:>5} ({v/tt:>5.1%})  {k}")
    print(f"\n■ 环节: " + " · ".join(f"{k} {v}({v/max(1,sum(agg['cat'].values())):.0%})"
                                    for k, v in agg["cat"].most_common()))


if __name__ == "__main__":
    main()
