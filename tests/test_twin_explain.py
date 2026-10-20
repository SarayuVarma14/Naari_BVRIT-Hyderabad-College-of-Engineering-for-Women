"""Fix 3: plain-language Digital Twin explanations (full app AND static preview). Needs playwright + chromium.
Run: python3 tests/test_twin_explain.py
Covers: Ananya v1/v2/v3 + every version comparison (both selection orders) against API ground truth; Meera (no saved twin);
Kavya (empty record); missing values / absent units / warnings / unknown fields (stubbed API); raw technical keys only inside the
expandable 'Technical details'; label parity between the server (PDF/brief) and the UI; scope statement parity between full app and preview;
timeline wording. Fix 1 / Fix 2 regressions are covered by test_brief_browser.py and the test_tour_*.py suites."""
import os, sys, re, json, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
RAW = re.compile(r"\b(cgm|clinical)\.[A-Za-z_0-9]|_pct\b|hours_since|feature change|feature\(s\)|change\(s\)")
OUT = """()=>{const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);const o=[];let n;while(n=w.nextNode()){const e=n.parentElement;if(!e||['SCRIPT','STYLE'].includes(e.tagName))continue;if(e.closest('details')&&!e.closest('summary'))continue;const s=n.textContent.trim();if(s)o.push(s)}return o}"""
async def api(pg, p): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", p)
fmt = lambda v: "{:,.2f}".format(v).rstrip("0").rstrip(".") if isinstance(v, float) else "{:,}".format(v)
flat = lambda t: re.sub(r"\s+", " ", t)
UN = lambda f: {"cgm.mean": " mg/dL", "cgm.tir_70_180_pct": " %", "cgm.tar_gt180_pct": " %", "cgm.n": "", "cgm.hours_since_last": " hours"}.get(f)
async def go(pg, base, pid, tab):
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/{tab}"); await pg.wait_for_selector("#tab .card, #tab .empty", timeout=15000); await pg.wait_for_timeout(500)
async def twin_checks(pg, base, label):
    S = await api(pg, "/api/patients/SYN-000/twin"); check([s["version"] for s in S] == [3, 2, 1], f"[{label}] fixture: Ananya has saved versions 3, 2, 1")
    await go(pg, base, "SYN-000", "twin"); txt = flat(await pg.inner_text("#tab"))
    out = await pg.evaluate(OUT); check(not [x for x in out if RAW.search(x)], f"[{label}] Ananya Twin default view: no raw field names outside 'Technical details' {[x for x in out if RAW.search(x)][:2]}")
    for p in ("Saved Twin state", "Changes between versions", "Source records", "research data summary, not a diagnosis", "not a validated forecast of PCOS progression", "Cycles and symptoms are not yet twin features", "Menstrual cycles", "Not yet (stored and shown elsewhere)", "does not by itself indicate improvement or deterioration", "recorded reason:"):
        check(p in txt, f"[{label}] Twin page states: '{p}'")
    check(await pg.locator("#tab details summary:has-text('Technical details')").count() >= 1, f"[{label}] a clearly labelled 'Technical details' expander exists")
    # version history
    for s in S: check(f"v{s['version']}" in txt and f"{len(s['changes'])} values added or changed" in txt, f"[{label}] version history row v{s['version']}: '{len(s['changes'])} values added or changed' with its recorded reason '{s['reason'][:30]}…'")
    # every comparison, both selection orders, against API ground truth
    n_ok = 0
    for a in (1, 2, 3):
        for b in (1, 2, 3):
            await pg.select_option("#ca", str(a)); await pg.select_option("#cb", str(b)); await pg.click("[data-act=cmp]"); await pg.wait_for_timeout(250)
            t = flat(await pg.inner_text("#cmp")); lo, hi = min(a, b), max(a, b)
            if a == b: check("Choose two different versions" in t, f"[{label}] compare {a} with {b}: asks for two different versions"); continue
            gt = await api(pg, f"/api/patients/SYN-000/twin/compare?a={lo}&b={hi}"); ok = f"Version {lo} (earlier) compared with version {hi} (later)" in t and "no longer available" not in t
            for d in gt["diff"]:
                u = UN(d["feature"])
                if d["feature"].startswith("clinical."):
                    unit = next((s["state"]["clinical"][d["feature"][9:]]["unit"] for s in S if s["version"] == hi and d["feature"][9:] in s["state"]["clinical"]), "")
                    u = " " + unit if unit else ""
                want = f"changed from {fmt(d['a'])}{u} to {fmt(d['b'])}{u}" if d["a"] is not None else f"is now {fmt(d['b'])}{u} (it was not recorded in the earlier version)"
                ok = ok and want in t
            ok = ok and f"{gt['unchanged_count']} other recorded" in t and not [x for x in await pg.evaluate(OUT) if RAW.search(x)]
            n_ok += ok; check(ok, f"[{label}] compare v{a} (Version) with v{b} (Compared with): rendered as v{lo}→v{hi}, previous/current values with units match the API ({len(gt['diff'])} differences)")
    srcs = flat(await pg.inner_text("#cmp"))
    await pg.select_option("#ca", "1"); await pg.select_option("#cb", "2"); await pg.click("[data-act=cmp]"); await pg.wait_for_timeout(250); t12 = flat(await pg.inner_text("#cmp"))
    check("synthetic_lab_report_scan.png p.1" in t12 and "result dated 2026-03-08" in t12, f"[{label}] v1→v2: each change names its source report/date")
    check("is now 6.1 %" in t12 and "HbA1c" in t12 and "not recorded in the earlier version" in t12, f"[{label}] v1→v2: HbA1c is described as newly recorded, in correct direction")
    await pg.select_option("#ca", "2"); await pg.select_option("#cb", "3"); await pg.click("[data-act=cmp]"); await pg.wait_for_timeout(250); t23 = flat(await pg.inner_text("#cmp"))
    check("demo-batch-2" in t23 and "not attributed to that batch alone" in t23 and "Number of valid glucose readings used changed from 2,880 to 4,032" in t23, f"[{label}] v2→v3: glucose changes name the newest data batch, 2,880 → 4,032 readings, no single-batch attribution")
    check(not re.search(r"improv|deteriorat|worse|better", t23.replace("does not by itself indicate improvement or deterioration", ""), re.I), f"[{label}] comparison text makes no improvement/deterioration claim")
    for s in S:
        await pg.click(f'[data-act=replay][data-v="{s["version"]}"]'); await pg.wait_for_timeout(250); rt = flat(await pg.inner_text("#replay")); ro = await pg.evaluate(OUT)
        check(not [x for x in ro if RAW.search(x)] and "mg/dL" in rt and "mmHg" in rt and "Technical details" in rt, f"[{label}] replay v{s['version']}: plain labels with units; raw keys only in Technical details")
        miss = s["state"]["missing"]; check((("Not recorded then: HbA1c" in rt) if miss == ["hba1c"] else ("Not recorded then" not in rt)), f"[{label}] replay v{s['version']}: missing information shown correctly ({miss})")
    return txt
async def empty_checks(pg, label):
    for pid, nm in (("SYN-001", "Meera"), ("SYN-002", "Kavya")):
        await go(pg, None, pid, "twin"); t = flat(await pg.inner_text("#tab")); Sm = await api(pg, f"/api/patients/{pid}/summary"); k = Sm["counts"]
        check((await api(pg, f"/api/patients/{pid}/twin")) == [], f"[{label}] fixture: {nm} has no saved Twin")
        check(("No Digital Twin version" in t or "no Digital Twin version has been built" in t or "No records yet" in t) and "research data summary, not a diagnosis" in t and "What the twin is built from" in t and "Cycles and symptoms are not yet twin features" in t, f"[{label}] {nm}: empty state explains what a Twin is built from and its scope")
        if pid == "SYN-001": check("Records are present for Meera Sharma (SYN-001)" in t and f"{k['verified_measurements']} verified measurement (1 lab result" in t and "no imported wearable data" in t, f"[{label}] Meera: records present but no Twin; states her actual records ({k['verified_measurements']} verified, no wearable data)")
        else: check("No records yet for Kavya Reddy (SYN-002)" in t and "Nothing has been recorded for this patient yet" in t, f"[{label}] Kavya: no records; names the patient and says nothing has been recorded")
        o = await pg.evaluate(OUT); check(not [x for x in o if RAW.search(x)] and "undefined" not in t and "NaN" not in t, f"[{label}] {nm}: no raw keys / undefined / NaN")
async def edge_checks(pg, label):
    await go(pg, None, "SYN-000", "twin")
    r = await pg.evaluate("""async()=>{const real=window.api;const snap={id:1,patient:'SYN-000',version:1,as_of:'2026-03-10T23:55:00Z',reason:'manual',
      state:{as_of:'2026-03-10T23:55:00Z',clinical:{'NoUnit Test':{value:5,unit:'',date:'2026-03-01'}},cgm:{n:10,mean:100,tir_70_180_pct:50,tar_gt180_pct:50,hours_since_last:9.5,sd:3},missing:['glucose time-series','hba1c','something else'],warnings:['CGM stale (>6h)','Unmapped warning text'],sources:{}},
      changes:[{feature:'cgm.mean',old:null,new:100,source:null},{feature:'cgm.n',old:12,new:null,source:\"glucose data batch 'x'\"},{feature:'clinical.NoUnit Test',old:4,new:5,source:'NoUnit Test result dated 2026-03-01 (x)'},{feature:'cgm.sd',old:1,new:3,source:'x'},{feature:'cgm.hours_since_last',old:2,new:9.5,source:''}]};
      window.api=async p=>p.endsWith('/twin')?[snap]:real(p);const el=document.createElement('div');document.body.appendChild(el);try{await VIEWS.twin(el)}finally{window.api=real}
      const vis=[];const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){const e=n.parentElement;if(e.closest('details')&&!e.closest('summary'))continue;if(n.textContent.trim())vis.push(n.textContent.trim())}
      const t=el.innerText;el.remove();return {vis,t}}""")
    t = flat(r["t"]); raw = [x for x in r["vis"] if RAW.search(x)]
    check(not raw, f"[{label}] stubbed snapshot (missing values, absent unit, unknown field, warnings): no raw keys outside Technical details {raw[:2]}")
    check("is now 100 mg/dL (it was not recorded in the previous version)" in t, f"[{label}] value with no previous record: 'is now 100 mg/dL (it was not recorded …)'")
    check("is not recorded in the later version (it was 12" in t, f"[{label}] value that disappeared: described as not recorded, previous value kept")
    check("changed from 4 to 5." in t and "undefined" not in t and "null" not in t and "NaN" not in t, f"[{label}] absent unit: value shown without a unit, no 'undefined'/'null'/'NaN'")
    check("An additional recorded value" in t, f"[{label}] unknown field name gets a neutral label (raw name only in Technical details)")
    check("This changed, but its cause cannot be determined" in t, f"[{label}] change with no recorded source: says cause cannot be determined")
    check("Glucose readings are stale: the newest reading is more than 6 hours before the version date." in t and "Unmapped warning text" in t, f"[{label}] warnings: known warning explained in plain language, unknown warning shown unchanged")
    check("Not recorded in the twin: glucose readings (none in the last 14 days)" in t and "Not recorded in the twin: HbA1c." in t and "Not recorded in the twin: something else." in t, f"[{label}] missing-information list uses readable names")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        # ---- static code-level checks against the real implementation ----
        ui = open(f"{ROOT}/web/ui2.js", encoding="utf-8").read(); srv = open(f"{ROOT}/naari/server.py", encoding="utf-8").read()
        feat = dict(re.findall(r"'(cgm\.[a-z0-9_]+)':\['([^']+)'", ui.split("const FEAT=")[1].split("};")[0])); fn = dict(re.findall(r'"(cgm\.[a-z0-9_]+)": \("([^"]+)"', srv.split("FEATN = ")[1].split("}\n")[0]))
        check(len(feat) == 5 and all(fn.get(k) == v for k, v in feat.items()), "UI labels and server (brief/PDF) labels are identical for every glucose summary field")
        from naari.twin import Twin
        T = Twin(os.path.join(tmp, "probe.db"), os.path.join(ROOT, "models/glucose_tar_2h.joblib")); import pandas as pd
        d = pd.read_csv(os.path.join(ROOT, "data/samples/demo_new_cgm_batch.csv")); d["ts"] = pd.to_datetime(d.timestamp); T.add_patient("P"); T.import_timeseries("P", d, "b", "t"); T.update("P", "2026-03-14T23:55:00Z")
        st = json.loads(T.db.execute("SELECT state FROM snapshots").fetchone()[0]); check(set("cgm." + k for k in st["cgm"]) <= set(feat), f"every field the Twin engine can store in its glucose summary has a plain-language label {sorted(set('cgm.' + k for k in st['cgm']) - set(feat))}")
        check(set(st) == {"as_of", "clinical", "cgm", "missing", "warnings", "sources"}, "engine confirms the saved Twin state holds only clinical results + glucose summary (no cycles, symptoms, heart rate or steps)")
        # ---- full app: reproduce the demo workflow through the real API ----
        wf = await pg.evaluate("""async()=>{const J=(p,o)=>fetch(p,{credentials:'same-origin',...o}).then(r=>r.json()),H={'Content-Type':'application/json'};
  const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());const d=await J('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',body:png});
  for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await J('/api/fields/'+f.id,{method:'POST',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
  const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await J('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',body:csv});
  const u=await J('/api/patients/SYN-000/twin/update',{method:'POST',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})});return u.version}""")
        check(wf == 3, "workflow through the real API produced Twin version 3")
        TL = await api(pg, "/api/patients/SYN-000/timeline"); tw = [e["detail"] for e in TL if e["category"] == "twin"]
        check(len(tw) == 3 and not any("feature" in x for x in tw) and all("values added or changed · recorded reason:" in x for x in tw), f"server timeline wording is plain for all three versions {tw[:1]}")
        full_scope = await twin_checks(pg, B, "full app"); await empty_checks(pg, "full app"); await edge_checks(pg, "full app")
        await go(pg, B, "SYN-000", "timeline"); tt = flat(await pg.inner_text("#tab")); check("feature change" not in tt and "values added or changed" in tt, "[full app] Medical timeline: twin events use plain wording")
        check(not errs, f"[full app] no JavaScript errors {errs[:2]}")
        # ---- preview ----
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; pv.on("pageerror", lambda e: perr.append(str(e))); await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient")
        check("STATIC READ-ONLY PREVIEW" in await pv.inner_text(".banner") and "Synthetic data" in await pv.inner_text(".banner"), "[preview] still visibly labelled read-only and synthetic")
        pv_scope = await twin_checks(pv, PV, "preview"); await empty_checks(pv, "preview"); await edge_checks(pv, "preview")
        await go(pv, PV, "SYN-000", "timeline"); tt = flat(await pv.inner_text("#tab")); check("feature change" not in tt and "values added or changed" in tt, "[preview] Medical timeline: twin events use plain wording")
        # scope parity (the explanatory text above the live numbers must be identical)
        sc = lambda t: t.split("What changed in version")[0].split("Built from data up to")[0]
        sa = lambda t: re.search(r"How to read this page\..*?PCOS progression\.", t).group(0); sb = lambda t: re.search(r"This is the current extent of the prototype\. Cycles and symptoms are not yet twin features\.", t).group(0)
        check(sa(full_scope) == sa(pv_scope) and sb(full_scope) == sb(pv_scope), "scope explanation is word-for-word identical in the full app and the static preview")
        check(not perr, f"[preview] no JavaScript errors {perr[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL FIX 3 CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
