"""Build a self-contained HTML report (report/index.html) from the eval + discovery outputs.

    python -m evals.run --out evals/results.md     # writes evals/results.json
    python -m discovery.analyse                    # writes discovery/report.json
    python -m evals.html                           # writes report/index.html (open it in a browser, works offline)
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "index.html"


def build() -> str:
    results = json.loads((ROOT / "evals" / "results.json").read_text())
    discovery = json.loads((ROOT / "discovery" / "report.json").read_text())
    history = json.loads((ROOT / "evals" / "holdout_history.json").read_text())
    slim = {"guarded": results["guarded"], "baseline": results["baseline"]}
    disc = {k: discovery[k] for k in ("n", "auto", "auto_correct", "auto_minutes", "routed", "routed_right", "unsafe",
                                      "flags", "seeded", "caught", "false_flags", "deviation_kinds", "gaps", "days")}
    data = json.dumps({"results": slim, "discovery": disc, "history": history}).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wattle Concierge Report</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--accent:#0f766e;--accent-soft:#e6f4f2;
--ok:#15803d;--ok-soft:#e8f5ec;--bad:#b42318;--bad-soft:#fdecea;--warn:#a15c07;--warn-soft:#fdf3e2;--agent:#eef2f7;--cust:#e6f4f2;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121211;--panel:#1b1b19;--ink:#ecebe6;--muted:#a3a29b;--line:#2e2d2a;--accent:#5eead4;--accent-soft:#12302c;
--ok:#4ade80;--ok-soft:#13291b;--bad:#f87171;--bad-soft:#331616;--warn:#fbbf24;--warn-soft:#2f2410;--agent:#20242b;--cust:#12302c}}
:root[data-theme=dark]{--bg:#121211;--panel:#1b1b19;--ink:#ecebe6;--muted:#a3a29b;--line:#2e2d2a;--accent:#5eead4;--accent-soft:#12302c;
--ok:#4ade80;--ok-soft:#13291b;--bad:#f87171;--bad-soft:#331616;--warn:#fbbf24;--warn-soft:#2f2410;--agent:#20242b;--cust:#12302c}
*{box-sizing:border-box}body{position:relative;margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 80px}h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:18px;margin:36px 0 12px}
.sub{color:var(--muted);margin:0 0 18px}.note{background:var(--warn-soft);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:13.5px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}.tile b{display:block;font-size:26px;letter-spacing:-.02em}
.tile span{color:var(--muted);font-size:13px}.tile.good b{color:var(--ok)}.tile.bad b{color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600;color:var(--muted);font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:0}.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13.5px}.flow div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 12px}
.flow .arrow{border:0;background:none;padding:0;color:var(--muted)}.flow .det{border-color:var(--accent);background:var(--accent-soft)}
.explorer{display:grid;grid-template-columns:300px 1fr;gap:14px;min-height:520px}@media(max-width:820px){.explorer{grid-template-columns:1fr}}
.list{background:var(--panel);border:1px solid var(--line);border-radius:12px;max-height:640px;overflow:auto}
.item{padding:9px 12px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:8px;align-items:baseline}.item:hover{background:var(--bg)}
.item.sel{background:var(--accent-soft)}.item .id{font-family:var(--mono);font-size:12.5px;min-width:34px}.item .t{font-size:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dot{width:8px;height:8px;border-radius:50%;flex:none;margin-top:5px}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.detail{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;overflow:auto;max-height:640px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{border:0;background:var(--panel);color:var(--ink);padding:6px 11px;font:inherit;font-size:13px;cursor:pointer}.seg button.on{background:var(--accent);color:var(--panel)}
select{font:inherit;font-size:13px;padding:5px 8px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}
.bubble{max-width:82%;padding:9px 12px;border-radius:12px;margin:8px 0;font-size:14px}.bubble.customer{background:var(--cust);margin-left:auto;border-bottom-right-radius:3px}
.bubble.agent{background:var(--agent);border-bottom-left-radius:3px}.who{font-size:11.5px;color:var(--muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:.05em}
.tool{font-family:var(--mono);font-size:12.5px;color:var(--muted);margin:6px 0 2px;padding-left:10px;border-left:2px solid var(--line)}
.gates{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 8px 10px}.gate{font-family:var(--mono);font-size:11.5px;padding:2px 6px;border-radius:6px;background:var(--ok-soft);color:var(--ok)}
.gate.fail{background:var(--bad-soft);color:var(--bad);font-weight:600}.gate.j{outline:1px dashed currentColor}
.badge{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;margin-right:6px}.badge.ok{background:var(--ok-soft);color:var(--ok)}.badge.bad{background:var(--bad-soft);color:var(--bad)}
.meta{font-size:13.5px;margin-bottom:10px}.meta code{font-family:var(--mono);font-size:12.5px}.guard{font-size:12.5px;color:var(--warn);margin:4px 0}
.bar{height:10px;background:var(--accent);border-radius:5px}.legend{font-size:12.5px;color:var(--muted);margin-top:6px}
.toggle{position:absolute;top:12px;right:12px;font-size:12px}.tw{overflow-x:auto;border-radius:12px}.tw table{min-width:640px}
.detail,.list{min-width:0}.explorer>*{min-width:0}.bubble{overflow-wrap:anywhere}.tool{overflow-wrap:anywhere}
</style></head><body>
<div class="seg toggle"><button data-th="light">Light</button><button data-th="dark">Dark</button></div>
<div class="wrap">
<h1>Wattle Money Concierge</h1>
<p class="sub">An FDE-style proof of work: a policy-bound AI support concierge for a fictional neobank, with an eval suite, simulated customers and a shadow-replay discovery study.</p>
<div class="note"><b>Mock data.</b> The customer, accounts, policy and ticket export are invented. Results come from an offline deterministic model, not an LLM.</div>
<div class="tiles" id="tiles"></div>

<h2>How a conversation flows</h2>
<div class="flow"><div>Customer message</div><span class="arrow">→</span><div>Input screen<br><small>care · hardship · complaint · injection</small></div><span class="arrow">→</span>
<div>Agent picks tools<br><small>may ask the customer</small></div><span class="arrow">→</span><div class="det">Write tool runs policy gates<br><small>against real state + transcript</small></div><span class="arrow">→</span>
<div>Output check<br><small>cited? no promises? no false claims?</small></div><span class="arrow">→</span><div>Reply or human handoff</div></div>

<h2>Fresh-holdout history</h2>
<p class="sub">Each holdout was committed before the change it tests and scored once. Then it became development data.</p>
<div class="tw"><table id="hist"></table></div>

<h2>Conversation explorer</h2>
<div class="controls"><div class="seg" id="mode"><button data-m="guarded" class="on">Concierge (guarded)</button><button data-m="baseline">Baseline (no gates)</button></div>
<select id="set"></select><select id="filter"><option value="all">All tickets</option><option value="fail">Misses only</option><option value="unsafe">Unsafe only</option></select></div>
<div class="explorer"><div class="list" id="list"></div><div class="detail" id="detail"></div></div>
<p class="legend">Gate chips: green = passed, red = the gate that decided. Dashed = judgement gate (uses the model's reading of the conversation).</p>

<h2>Discovery: shadow replay on a 90-day ticket export</h2>
<div class="tiles" id="dtiles"></div>
<h2 style="font-size:16px">Where it can't yet</h2><div class="tw"><table id="gaps"></table></div>
<h2 style="font-size:16px">Human deviations from written policy</h2><div class="tw"><table id="dev"></table></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
try{const t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}catch(e){}
document.querySelectorAll('[data-th]').forEach(b=>b.onclick=()=>{document.documentElement.dataset.theme=b.dataset.th;try{localStorage.setItem('theme',b.dataset.th)}catch(e){}});
const H=D.history.filter(h=>h.model!=='v1');const unseen=H.reduce((a,h)=>a+h.passed,0),unseenN=H.reduce((a,h)=>a+h.tickets,0),unseenUnsafe=H.reduce((a,h)=>a+h.unsafe,0);
const g=D.results.guarded,b=D.results.baseline;const cnt=(a,f)=>a.filter(f).length;
const tiles=[[`${unseen}/${unseenN}`,'unseen holdout tickets passed (v2–v3.1, each scored once)','good'],
[`${unseenUnsafe}`,'unsafe actions on unseen holdouts (1, fixed in v3.1)',''],
[`${cnt(g,r=>r.unsafe)} vs ${cnt(b,r=>r.unsafe)}`,`unsafe actions now: concierge vs same agent without gates (${g.length} tickets)`,'bad'],
[`${cnt(g,r=>r.passed)}/${g.length}`,'all tickets passing now (incl. seen sets)','']];
$('#tiles').innerHTML=tiles.map(([v,l,c])=>`<div class="tile ${c}"><b>${v}</b><span>${l}</span></div>`).join('');
$('#hist').innerHTML='<tr><th>Model</th><th>What changed</th><th>Holdout</th><th class="num">Passed</th><th class="num">Unsafe</th><th>Misses → human</th><th>Notes</th></tr>'+
D.history.map(h=>`<tr><td><b>${h.model}</b><br><code>${h.commit}</code></td><td>${esc(h.change)}</td><td>${h.holdout}</td><td class="num">${h.passed}/${h.tickets}</td><td class="num">${h.unsafe}</td><td>${h.misses_to_human}</td><td>${esc(h.note)}</td></tr>`).join('');
const sets=[...new Set(g.map(r=>r.set))];$('#set').innerHTML='<option value="all">All sets</option>'+sets.map(s=>`<option>${s}</option>`).join('');
let mode='guarded',sel=null;
function rows(){const src=D.results[mode],s=$('#set').value,f=$('#filter').value;return src.filter(r=>(s==='all'||r.set===s)&&(f==='all'||(f==='fail'&&!r.passed)||(f==='unsafe'&&r.unsafe)))}
function exp(e){return e.action+(e.txn_id?`(${e.txn_id})`:'')+(e.queue?`→${e.queue}`:'')}
function renderList(){const R=rows();if(!R.find(r=>r.id===sel))sel=R[0]&&R[0].id;
$('#list').innerHTML=R.map(r=>{const first=(r.audit.find(a=>a.say)||{}).text||'';const msg=r.message||'';return `<div class="item ${r.id===sel?'sel':''}" data-id="${r.id}"><span class="dot ${r.passed?'ok':'bad'}"></span><span class="id">${r.id}</span><span class="t">${esc(r.category)} · ${esc(r.got)}</span></div>`}).join('')||'<div class="item">No tickets</div>';
document.querySelectorAll('.item[data-id]').forEach(el=>el.onclick=()=>{sel=el.dataset.id;renderList()});renderDetail()}
function renderDetail(){const r=D.results[mode].find(x=>x.id===sel);if(!r){$('#detail').innerHTML='';return}
let h=`<div class="meta"><span class="badge ${r.passed?'ok':'bad'}">${r.passed?'pass':'miss'}</span><b>${r.id}</b> · ${esc(r.set)} · customer <code>${r.customer}</code><br>Expected <code>${esc(exp(r.expect))}</code> · Got <code>${esc(r.got)}</code>${r.notes.length?`<br><span style="color:var(--bad)">${esc(r.notes.join('; '))}</span>`:''}</div>`;
const firstMsg=D.results.guarded.find(x=>x.id===r.id);
h+=`<div class="bubble customer"><div class="who">Customer</div>${esc(ticketText(r.id))}</div>`;
let pending='';
for(const a of r.audit){if(a.say){h+=`<div class="bubble ${a.say}"><div class="who">${a.say}</div>${esc(a.text)}</div>`}
else if(a.workflow){pending='<div class="gates">'+a.trace.map(t=>`<span class="gate ${t.passed?'':'fail'} ${t.kind==='judgement'?'j':''}" title="${t.kind}">${t.policy_ref} ${t.gate.replace(/_/g,' ')}</span>`).join('')+'</div>'}
else{const res=a.result||{};h+=`<div class="tool">${esc(a.tool)}(${esc(JSON.stringify(a.args).slice(0,90))}) → <b>${esc(res.status)}</b>${res.policy_refs?' ['+res.policy_refs.join(', ')+']':''}</div>`+pending;pending=''}}
for(const e of r.guardrail_events)h+=`<div class="guard">⚑ ${esc(e)}</div>`;$('#detail').innerHTML=h}
const TEXT={};
function ticketText(id){return TEXT[id]||''}
document.querySelectorAll('#mode button').forEach(bt=>bt.onclick=()=>{mode=bt.dataset.m;document.querySelectorAll('#mode button').forEach(x=>x.classList.toggle('on',x===bt));renderList()});
$('#set').onchange=renderList;$('#filter').onchange=renderList;
for(const r of g)TEXT[r.id]=r.text;renderList();
const d=D.discovery,mon=30/d.days;
$('#dtiles').innerHTML=[[`${d.auto_correct}/${d.n}`,'tickets the concierge resolves end to end, matching policy','good'],[`${Math.round(d.auto_minutes*mon/60)} h`,'agent-hours a month at recorded handle times',''],
[`${d.unsafe}`,'unsafe actions in replay',''],[`${d.caught}/${d.seeded}`,`seeded human policy deviations caught (${d.false_flags} false flags)`,'good']].map(([v,l,c])=>`<div class="tile ${c}"><b>${v}</b><span>${l}</span></div>`).join('');
const maxg=Math.max(...Object.values(d.gaps).map(v=>v.tickets));
$('#gaps').innerHTML='<tr><th>Request type</th><th class="num">Tickets / 90d</th><th style="width:40%">Share</th></tr>'+Object.entries(d.gaps).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td class="num">${v.tickets}</td><td><div class="bar" style="width:${100*v.tickets/maxg}%"></div></td></tr>`).join('');
$('#dev').innerHTML='<tr><th>Pattern</th><th class="num">Count</th></tr>'+d.deviation_kinds.map(([k,c])=>`<tr><td>${esc(k)}</td><td class="num">${c}</td></tr>`).join('');
</script></body></html>"""


if __name__ == "__main__":
    main()
