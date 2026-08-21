"""session 阅读器：把一场会话投影成可读的单文件 HTML（反思工作流的「看」）。

产物含对话原文 → 只准落私有目录（harvest_out_dir/sessions/）。
thinking 流不渲染只计数；工具调用折叠；锚点 = 轮次导航 + 返工热点。

用法:
  python reader.py --date 2026-08-20          # 当日全部活动 session
  python reader.py --workspace <ws> --sid <sid>
"""
import argparse
import collections
import datetime
import html
import json
from pathlib import Path

from harvest import active_on, iter_event_files, source_path, summarize, short_path
from meter_config import load as load_cfg

CFG = load_cfg()
CAT_COLOR = {"retrieval": "#3B82F6", "mutation": "#F59E0B", "verification": "#10B981",
             "execution": "#8B5CF6", "coordination": "#64748B", "other": "#94A3B8",
             "unknown": "#EF4444"}
CAT_CN = {"retrieval": "检索", "mutation": "变更", "verification": "校验",
          "execution": "执行", "coordination": "协调", "other": "其他", "unknown": "未知"}

CSS = """
:root{--bg:#F8FAFC;--card:#fff;--line:#E5E8EE;--ink:#1E293B;--ink2:#0F172A;
--mut:#64748B;--accent:#4F46E5;--accent-bg:#EEF2FF;--code:#F1F5F9}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-size:16px;line-height:1.75;
font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
main{max-width:780px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:22px;line-height:1.4;color:var(--ink2);letter-spacing:-.02em;margin:8px 0 4px}
.meta{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
box-shadow:0 1px 2px rgba(16,24,40,.05),0 2px 6px rgba(16,24,40,.06);padding:16px 20px;margin:16px 0}
.kpis{display:flex;flex-wrap:wrap;gap:16px 28px;margin-top:8px}
.kpi b{display:block;font-size:20px;color:var(--ink2);font-variant-numeric:tabular-nums}
.kpi span{font-size:12px;color:var(--mut)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.chip{font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:999px;
padding:2px 12px;color:var(--ink);text-decoration:none}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip .n{color:var(--mut);font-variant-numeric:tabular-nums}
.turn{background:var(--accent-bg);border-left:3px solid var(--accent);border-radius:0 12px 12px 0;
padding:12px 18px;margin:28px 0 12px;white-space:pre-wrap;word-break:break-word}
.turn .who{font-size:12px;color:var(--accent);font-weight:600;display:block;margin-bottom:2px}
.ai{margin:12px 0;white-space:pre-wrap;word-break:break-word}
.ai .who{font-size:12px;color:var(--mut);display:block}
details.tools{margin:10px 0;border:1px solid var(--line);border-radius:8px;background:var(--card)}
details.tools>summary{cursor:pointer;padding:6px 14px;font-size:13px;color:var(--mut);list-style:none}
details.tools>summary::-webkit-details-marker{display:none}
details.tools[open]>summary{border-bottom:1px solid var(--line)}
.trow{display:flex;gap:8px;align-items:baseline;padding:3px 14px;font-size:13px}
.trow .dot{width:8px;height:8px;border-radius:99px;flex:none;align-self:center}
.trow .nm{color:var(--ink2);font-weight:600;flex:none}
.trow .tg{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--mut);
overflow-wrap:anywhere}
.tres{padding:0 14px 6px 30px;font-size:12px;color:var(--mut)}
.tres pre{background:var(--code);border-radius:6px;padding:6px 10px;margin:4px 0;
white-space:pre-wrap;word-break:break-word;font-size:12px}
details.sysmsg{margin:10px 0}
details.sysmsg>summary{cursor:pointer;font-size:12px;color:var(--mut)}
details.sysmsg pre{background:var(--code);border-radius:8px;padding:8px 12px;
white-space:pre-wrap;word-break:break-word;font-size:12px;color:var(--mut)}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--mut);margin-top:8px}
.legend i{width:8px;height:8px;border-radius:99px;display:inline-block;margin-right:4px}
footer{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:12px}
"""


def esc(s):
    return html.escape(s or "", quote=False)


def hhmm(ts):
    return (ts or "")[11:16]


def dot(cat):
    return f'<span class="dot" style="background:{CAT_COLOR.get(cat, "#94A3B8")}"></span>'


def render(meta, items, stats):
    body, turn_chips = [], []
    rework = collections.Counter()
    first_anchor = {}
    turn_i = 0
    i = 0
    n = len(items)
    while i < n:
        it = items[i]
        k = it["kind"]
        if k == "user":
            if it.get("meta"):
                body.append(f'<details class="sysmsg"><summary>系统注入 · {len(it["text"])} 字'
                            f' · {hhmm(it["ts"])}</summary><pre>{esc(it["text"][:4000])}</pre></details>')
            else:
                turn_i += 1
                label = esc(it["text"][:22].replace("\n", " "))
                turn_chips.append(f'<a class="chip" href="#turn-{turn_i}"><span class="n">{turn_i}</span> {label}</a>')
                body.append(f'<div class="turn" id="turn-{turn_i}"><span class="who">你 · {hhmm(it["ts"])}</span>'
                            f'{esc(it["text"])}</div>')
            i += 1
        elif k == "assistant":
            body.append(f'<div class="ai"><span class="who">AI · {hhmm(it["ts"])}</span>{esc(it["text"])}</div>')
            i += 1
        else:
            # 连续的 tool_call / tool_result / file_event 压成一组
            j = i
            cats = collections.Counter()
            rows = []
            while j < n and items[j]["kind"] in ("tool_call", "tool_result", "file_event"):
                t = items[j]
                if t["kind"] == "tool_call":
                    cats[t["category"]] += 1
                    if t["category"] == "mutation" and t.get("target"):
                        sp = short_path(t["target"])
                        rework[sp] += 1
                        first_anchor.setdefault(sp, f"g{i}")
                    # 壳命令显示整条 raw（target 只是首 token，如 cd）；文件工具显示 target
                    tg = esc((t.get("raw") or t.get("target") or "")[:160])
                    rows.append(f'<div class="trow">{dot(t["category"])}'
                                f'<span class="nm">{esc(t["name"])}</span><span class="tg">{tg}</span></div>')
                elif t["kind"] == "tool_result":
                    ok = "ok" if t.get("ok") else "<b style=\"color:#EF4444\">出错</b>"
                    row = f'<div class="tres">└ 结果 {ok} · {t.get("size", 0)} 字</div>'
                    pv = esc(t.get("preview") or "")
                    if pv and not t.get("ok"):  # 只有出错的结果值得看原文
                        row += f'<div class="tres"><pre>{pv}</pre></div>'
                    rows.append(row)
                else:
                    sp = short_path(t.get("path") or "")
                    rework[sp] += 1
                    first_anchor.setdefault(sp, f"g{i}")
                    rows.append(f'<div class="trow">{dot("mutation")}<span class="nm">写入</span>'
                                f'<span class="tg">{esc(sp)}</span></div>')
                j += 1
            summ = " · ".join(f"{CAT_CN.get(c, c)} {v}" for c, v in cats.most_common()) or "结果/文件事件"
            body.append(f'<details class="tools" id="g{i}"><summary>{j - i} 条工具活动 —— {summ}</summary>'
                        + "".join(rows) + "</details>")
            i = j

    hot = [(k2, v) for k2, v in rework.most_common(6) if v >= 2]
    hot_chips = "".join(f'<a class="chip" href="#{first_anchor[k2]}">↻{v} {esc(k2)}</a>' for k2, v in hot)
    legend = "".join(f'<span><i style="background:{c}"></i>{CAT_CN[k2]}</span>'
                     for k2, c in CAT_COLOR.items() if k2 != "other")
    m = meta["m"]
    kpis = [("步", meta["steps"]), ("轮", meta["turns"]), ("并行宽度", m["width"]),
            ("检索目标", m["files_read"]), ("返工", m["rework"]), ("成本$", f"{meta['cost']:.2f}")]
    kpi_html = "".join(f'<div class="kpi"><b>{v}</b><span>{k2}</span></div>' for k2, v in kpis)

    return f"""<!DOCTYPE html>
<html lang="zh-Hans"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(meta['title'])}</title><style>{CSS}</style></head><body><main>
<h1>{esc(meta['title'])}</h1>
<div class="meta">{esc(meta['ws'])} / {esc(meta['sid'])} · 环节 {esc(meta['cat_line'])}
{' · 省略思考 ' + str(stats['thinking_blocks']) + ' 段' if stats['thinking_blocks'] else ''}</div>
<div class="card"><div class="kpis">{kpi_html}</div><div class="legend">{legend}</div></div>
{'<div class="chips">' + hot_chips + '</div>' if hot_chips else ''}
<div class="chips">{''.join(turn_chips)}</div>
{''.join(body)}
<footer>session-meter 阅读页 · 含对话原文，只许留在私有目录 · 生成于 {datetime.date.today().isoformat()}</footer>
</main></body></html>"""


def generate(f, ws, is_sub, events, day=None):
    from ingest.adapter_claude import transcript
    meta = summarize(f, ws, is_sub, events)
    items, stats = transcript(source_path(f))
    # 目录按查询日（收割单链接口径）；跨天 session 会在多天各存一份，一致性优先
    day = day or (events[0].get("ts_first") or "")[:10] or "undated"
    out = Path(CFG["harvest_out_dir"]) / "sessions" / day / f"{f.stem}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(meta, items, stats), encoding="utf-8")
    return out


def generate_for_date(day):
    outs = []
    for f, ws, is_sub in iter_event_files():
        try:
            events = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        except OSError:
            continue
        if not events or events[0].get("ev") != "session" or not active_on(events[0], day):
            continue
        if events[0].get("harness") != "claude-code":
            continue
        try:
            outs.append(generate(f, ws, is_sub, events, day))
        except Exception as e:
            print(f"  reader ERR {f.stem}: {e}")
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--workspace")
    ap.add_argument("--sid")
    a = ap.parse_args()
    if a.workspace and a.sid:
        for f, ws, is_sub in iter_event_files():
            if ws == a.workspace and f.stem == a.sid:
                events = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
                print(generate(f, ws, is_sub, events))
                return
        raise SystemExit("找不到该 session 的事件文件（先跑 scan.py）")
    day = a.date or datetime.date.today().isoformat()
    outs = generate_for_date(day)
    print(f"{len(outs)} 个阅读页 → {Path(CFG['harvest_out_dir']) / 'sessions' / day}")


if __name__ == "__main__":
    main()
