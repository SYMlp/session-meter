"""今日收割单 · 机械档（零模型算力）。

扫当日有活动的全部 session（含子 agent 层），每 session 一行：
摘要 / 步数 / 判据五件套 / 环节占比 / 动过的文件；汇总头带变更:校验比与文件 TOP。

用法:
  python harvest.py                 # 今天；先增量 scan 再出单
  python harvest.py --date 2026-08-20 --no-scan
  python harvest.py --out some.md   # 覆盖 config 的 harvest_out_dir

口径（v1，诚实标注）：
  - 「当日有活动」= session 事件区间 [ts_first, ts_last] 覆盖目标日；
    统计是**整场**口径，跨天 session 的数字含它在其他日子的部分。
  - 摘要优先 AI 标题，缺失时从源日志读首条用户消息（宿主特异展示增强，
    仅 claude-code 有；本单含自然语言与路径 → 只准落私有目录）。
"""
import argparse
import collections
import datetime
import json
import subprocess
import sys
from pathlib import Path

from ingest.adapter_claude import first_user_text
from ingest.canonical import CANON_VERSION, MUTATION
from ledger import session_digest
from meter_config import load as load_cfg
from metrics.criteria import measure

CFG = load_cfg()
EVENTS = Path(CFG["data_dir"]) / "events" / f"v{CANON_VERSION}"
CAT_ABBR = {"retrieval": "检", "mutation": "改", "verification": "验",
            "execution": "跑", "coordination": "协", "other": "他", "unknown": "?"}


def short_path(t):
    parts = (t or "").replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else (t or "")


def active_on(sess, day):
    a, b = (sess.get("ts_first") or "")[:10], (sess.get("ts_last") or "")[:10]
    return bool(a) and a <= day <= (b or a)


def iter_event_files():
    for f in EVENTS.rglob("*.jsonl"):
        rel = f.relative_to(EVENTS)
        ws = rel.parts[0]
        is_sub = "_subagents" in rel.parts
        yield f, ws, is_sub


def source_path(f):
    """事件文件路径 → 源日志路径（claude-code 布局约定，用于摘要 peek）。"""
    root = Path(CFG["claude_projects_root"])
    rel = f.relative_to(EVENTS)
    if "_subagents" in rel.parts:  # <ws>/_subagents/<mother-sid>/agent-x.jsonl
        return root / rel.parts[0] / rel.parts[2] / "subagents" / rel.name
    return root / rel.parts[0] / rel.name


def summarize(f, ws, is_sub, events):
    sess = events[0]
    d = session_digest(events)
    m = measure(events)
    title = (d["title"] or "").strip()
    if not title and sess.get("harness") == "claude-code":
        title = first_user_text(source_path(f))
    ctot = sum(d["cat"].values())
    cat = " ".join(f"{CAT_ABBR.get(k, k)}{v/ctot:.0%}" for k, v in d["cat"].most_common())
    files = " · ".join(f"{k}×{v}" if v > 1 else k for k, v in d["edits"].most_common(5))
    cross_day = (sess.get("ts_first") or "")[:10] != (sess.get("ts_last") or "")[:10]
    return {
        "ws": ws, "sub": is_sub, "sid": f.stem, "title": title or "(无标题)",
        "steps": d["steps"], "turns": d["turns"], "cost": d["cost"],
        "m": m, "cat_line": cat or "-", "files_line": files or "-",
        "edits": d["edits"], "cat": d["cat"], "cross_day": cross_day,
        "edit_paths": collections.Counter(),  # 全路径版，供跨 session TOP
    }


def collect(day):
    rows = []
    for f, ws, is_sub in iter_event_files():
        try:
            events = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        except OSError:
            continue
        if not events or events[0].get("ev") != "session":
            continue
        if not active_on(events[0], day):
            continue
        r = summarize(f, ws, is_sub, events)
        for e in events:
            # 只信真实文件工具的 target；壳命令 target 是命令首 token，不是路径（schema「target 口径边界」）
            if e["ev"] == "tool.call" and e.get("name") in MUTATION and e.get("target"):
                r["edit_paths"][short_path(e["target"])] += 1
            elif e["ev"] == "file.event" and e.get("path"):
                r["edit_paths"][short_path(e["path"])] += 1
        rows.append(r)
    return rows


def render(day, rows):
    mains = [r for r in rows if not r["sub"]]
    subs = [r for r in rows if r["sub"]]
    tot_steps = sum(r["steps"] for r in rows)
    tot_cost = sum(r["cost"] for r in rows)
    cat_all = collections.Counter()
    files_all = collections.Counter()
    for r in rows:
        cat_all.update(r["cat"])
        files_all.update(r["edit_paths"])
    mut, ver = cat_all.get("mutation", 0), cat_all.get("verification", 0)
    ws_count = len({r["ws"] for r in rows})

    L = [f"# 今日收割单 · {day}", "",
         f"- session：主 **{len(mains)}** + 子 agent **{len(subs)}**，跨 {ws_count} 个工作区",
         f"- 总步数 **{tot_steps}**，估算成本 **${tot_cost:.2f}**（整场口径，跨天场含他日部分）",
         f"- 变更:校验 = **{mut}:{ver}**" + (f"（{mut/max(1,ver):.1f}:1）" if ver else "（校验红绿灯没开）"),
         ""]
    if files_all:
        L.append("**今日动得最多的文件**（跨 session，路径尾两段）：")
        for k, v in files_all.most_common(10):
            L.append(f"- {v}× `{k}`")
        L.append("")

    L += ["| 工作区 | 摘要 | 轮 | 步 | 五件套 d/w/r/t/rw | 环节 | 动过的文件 |",
          "|:--|:--|--:|--:|:--|:--|:--|"]
    for r in sorted(rows, key=lambda r: -r["steps"]):
        m = r["m"]
        five = f"{m['depth']}/{m['width']}/{m['files_read']}/{m['ttfe_tok']//1000}k/{m['rework']}"
        if not m["edited"]:
            five += " (未编辑)"
        ws = r["ws"] + (" ↳子" if r["sub"] else "") + (" ⏳跨天" if r["cross_day"] else "")
        title = r["title"][:60].replace("|", "\\|")
        files = r["files_line"].replace("|", "\\|")
        L.append(f"| {ws} | {title} | {r['turns']} | {r['steps']} | {five} | {r['cat_line']} | {files} |")

    L += ["",
          "> 五件套：d 串行深度 / w 并行宽度 / r 检索去重目标 / t 首编前输入(千tok) / rw 同目标返工。",
          "> 机械档零模型算力；只量「省不省」，不量「对不对」。本单含自然语言摘要与路径，**只准留在私有目录**。"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--out", help="覆盖 config 的 harvest_out_dir/<date>.md")
    a = ap.parse_args()

    if not a.no_scan:
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "scan.py"), "--all"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            sys.exit(1)
        print(r.stdout.strip().splitlines()[-3] if r.stdout.strip() else "scan done")

    rows = collect(a.date)
    md = render(a.date, rows)
    out = Path(a.out) if a.out else Path(CFG["harvest_out_dir"]) / f"{a.date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"{len(rows)} 个当日活动 session → {out}")


if __name__ == "__main__":
    main()
