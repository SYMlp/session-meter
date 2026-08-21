"""冒烟测试：只用自造 fixture，跑通 适配器 → scan → 判据五件套 → report 全链。

用法：python tests/test_smoke.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
MAIN = FIXTURES / "projects" / "demo-project" / "11111111-1111-1111-1111-111111111111.jsonl"
SUB = FIXTURES / "projects" / "demo-project" / "11111111-1111-1111-1111-111111111111" / "subagents" / "agent-22222222.jsonl"
PI = FIXTURES / "pi" / "pi-demo-session.jsonl"

# —— 配置外置：临时 config 指向 fixture，data 落临时目录 ——
tmp = Path(tempfile.mkdtemp(prefix="lens-smoke-"))
cfg = tmp / "config.yaml"
cfg.write_text(
    f"claude_projects_root: {FIXTURES / 'projects'}\ndata_dir: {tmp / 'data'}\n",
    encoding="utf-8")
os.environ["SESSION_METER_CONFIG"] = str(cfg)

from ingest.adapter_claude import parse as parse_claude  # noqa: E402
from ingest.adapter_pi import parse as parse_pi          # noqa: E402
from metrics.criteria import measure                     # noqa: E402

# —— 适配器 #1：requestId 聚合（一次调用被写成多条记录 → 一个 step）——
events, stats = parse_claude(MAIN)
steps = [e for e in events if e["ev"] == "step"]
assert len(steps) == 3, f"step 应聚合为 3，得到 {len(steps)}"
assert steps[0]["records"] == 2 and steps[0]["tool_calls"] == 2, "并行宽度被记录打散了"
calls = {e["name"]: e for e in events if e["ev"] == "tool.call"}
assert calls["Read"]["category"] == "retrieval"
assert calls["Edit"]["category"] == "mutation"
assert calls["Bash"]["category"] == "verification", "cd 前缀没剥掉，pytest 被遮蔽"
assert events[0]["ai_title"] == "修复 demo 测试"
assert any(e["ev"] == "file.event" for e in events)
# 隐私不变量：用户原话不落中立事件层
assert "帮我修" not in json.dumps(events, ensure_ascii=False)

# —— 判据五件套 ——
m = measure(events)
assert m["depth"] == 3 and m["edited"]
assert m["width"] == round(4 / 3, 3)
assert m["files_read"] == 2, m
assert m["ttfe_tok"] == (100 + 1000 + 200) + (120 + 2000 + 100), m
# Claude 侧 tool.call 与 file.event 双轨都发（现实如此）：同一文件计 2 次 → rework=1
assert m["rework"] == 1, m

# —— 子 agent 层（坑二）——
sub_events, sub_stats = parse_claude(SUB)
assert sub_events[0]["is_subagent"] is True
assert sub_stats["sidechain"] > 0

# —— 适配器 #3：Pi 树形分支 ——
pi_events, pi_stats = parse_pi(PI)
assert pi_stats["off_path"] == 1, "被放弃的分支应被排除并计数"
pi_steps = [e for e in pi_events if e["ev"] == "step"]
assert len(pi_steps) == 2 and pi_steps[0]["tool_calls"] == 1
assert {e["name"]: e["category"] for e in pi_events if e["ev"] == "tool.call"}["read"] == "retrieval"

# —— scan → report 端到端（子进程，吃同一份 config）——
env = os.environ.copy()
r = subprocess.run([sys.executable, str(ROOT / "scan.py"), "--all"],
                   capture_output=True, text=True, env=env, cwd=str(ROOT))
assert r.returncode == 0, r.stderr
assert "parsed=2" in r.stdout, r.stdout  # 母会话 + 子 agent 各一份
out_dir = tmp / "data" / "events"
assert list(out_dir.rglob("*.jsonl")), "scan 没落事件文件"

r2 = subprocess.run([sys.executable, str(ROOT / "report.py")],
                    capture_output=True, text=True, env=env, cwd=str(ROOT))
assert r2.returncode == 0, r2.stderr
assert "demo-project" in r2.stdout

r3 = subprocess.run([sys.executable, str(ROOT / "metrics" / "criteria.py"), "--scope", "session"],
                    capture_output=True, text=True, env=env, cwd=str(ROOT))
assert r3.returncode == 0, r3.stderr

# —— 展示通道 transcript（与 canonical 相反：这条通道就是要原文）——
from ingest.adapter_claude import transcript  # noqa: E402
items, tstats = transcript(MAIN)
kinds = [it["kind"] for it in items]
assert kinds.count("user") == 1 and kinds.count("tool_call") == 4
assert any(it["kind"] == "user" and "帮我修" in it["text"] for it in items)
assert any(it["kind"] == "tool_call" and it["category"] == "verification" for it in items)
assert kinds.count("file_event") == 1

# —— reader：阅读页生成（产物含原文 → 只落私有目录，这里是临时目录）——
import reader  # noqa: E402
pages = reader.generate_for_date("2026-08-20")
assert len(pages) == 2, f"母会话+子 agent 应各一页，得到 {len(pages)}"
page = next(p for p in pages if "11111111" in p.name).read_text(encoding="utf-8")
assert "帮我修" in page and "<details" in page and "pytest" in page
assert 'charset="utf-8"' in page and "zh-Hans" in page

print("SMOKE OK —— 适配器×2 / 判据五件套 / scan / report / criteria / transcript / reader 全通")
