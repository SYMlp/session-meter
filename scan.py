"""raw 转写 → canonical 事件（增量）。

用法：
  python scan.py --all
  python scan.py --project <project-slug> --force
日志根目录与数据目录见 config.example.yaml。
"""
import argparse
import json
import sys
import time
from pathlib import Path

from ingest.adapter_claude import parse
from ingest.canonical import CANON_VERSION
from lens_config import load

CFG = load()
SRC = Path(CFG["claude_projects_root"])
DATA = Path(CFG["data_dir"])
OUT = DATA / "events" / f"v{CANON_VERSION}"
MANIFEST = DATA / f"manifest.v{CANON_VERSION}.json"


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if not (a.all or a.project):
        ap.error("需要 --all 或 --project")

    man = load_manifest()
    projects = [SRC / a.project] if a.project else sorted(p for p in SRC.iterdir() if p.is_dir())
    # 顶层 = 母会话；<session-uuid>/subagents/agent-*.jsonl = 子 agent（一个子 agent 一个文件）
    files = [f for p in projects
             for f in sorted(p.glob("*.jsonl")) + sorted(p.glob("*/subagents/*.jsonl"))]
    if a.limit:
        files = files[:a.limit]

    t0 = time.time()
    done = skipped = 0
    agg = {"lines": 0, "bad_json": 0, "degraded": 0, "sidechain": 0, "events": 0}
    for i, f in enumerate(files, 1):
        st = f.stat()
        key = str(f)
        sig = [st.st_mtime_ns, st.st_size]
        if not a.force and man.get(key, {}).get("sig") == sig:
            skipped += 1
            continue
        try:
            events, stats = parse(f)
        except Exception as e:
            print(f"  ERR {f.name}: {e}", file=sys.stderr)
            continue
        if f.parent.name == "subagents":
            d = OUT / f.parents[2].name / "_subagents" / f.parents[1].name
        else:
            d = OUT / f.parent.name
        d.mkdir(parents=True, exist_ok=True)
        with open(d / (f.stem + ".jsonl"), "w", encoding="utf-8") as w:
            for e in events:
                w.write(json.dumps(e, ensure_ascii=False) + "\n")
        man[key] = {"sig": sig, "events": len(events), "stats": stats}
        for k in agg:
            agg[k] += stats.get(k, 0) if k != "events" else len(events)
        done += 1
        if done % 25 == 0:
            print(f"  [{i}/{len(files)}] {done} parsed, {time.time()-t0:.0f}s")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"parsed={done} skipped={skipped} in {time.time()-t0:.0f}s")
    print("stats:", agg)
    if agg["lines"]:
        print(f"bad_json率 {agg['bad_json']/agg['lines']:.4%} · degraded率 {agg['degraded']/agg['lines']:.4%}")


if __name__ == "__main__":
    main()
