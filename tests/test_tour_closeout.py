"""Fix 2 close-out: (1) visible launcher on overviews, (2) singular/plural grammar, (3) Data-quality wording in preview/full app (ready + blocked).
Run: python3 tests/test_tour_closeout.py"""
import os, sys, re, json, html, time, sqlite3, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
strip = lambda x: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
async def api(pg, p): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", p)
async def post(pg, p, b): return await pg.evaluate("([p,b])=>fetch(p,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.status)", [p, b])
async def card(pg):
    await pg.wait_for_selector("#tour p:not(:has-text('Loading'))", timeout=20000)
    return {"full": re.sub(r"\s+", " ", await pg.inner_text("#tour p")).strip(), "small": await pg.inner_text("#tour small:first-child"),
            "cap": await pg.evaluate("()=>[...document.querySelectorAll('#tour p .cap')].map(e=>e.textContent).join(' ')")}
async def cards(pg):                                   # run all 7 steps of the real tour, return their rendered texts
    out = []
    for i in range(7):
        c = await card(pg); await pg.wait_for_selector(".pbar"); await pg.wait_for_timeout(450); out.append(c)
        if i < 6: await pg.click("#tour [data-act=tourNext]")
    return out
BUILD = "async([pid,prev])=>{const sum=await api(`/api/patients/${pid}/summary`);const out=[];for(const s of TSTEPS){out.push(await s.text({pid,sum,name:sum.profile.name,prev}))}return out}"
# ---- independent grammar oracle (does not use the tour code) ----
async def counts(pg, pid):
    S = await api(pg, f"/api/patients/{pid}/summary"); M = [m for m in await api(pg, f"/api/patients/{pid}/measurements") if m["verified"]]; by = {}
    for m in M: by.setdefault(m["test"].lower(), []).append(m)
    L = [sorted(v, key=lambda x: x["date"]) for v in by.values()]; Q = await api(pg, f"/api/patients/{pid}/quality"); CS = await api(pg, f"/api/patients/{pid}/cycles/summary")
    return {"vm": len(M), "nt": len(L), "ns": sum(1 for v in L if len(v) == 1), "nr": sum(1 for v in L if v[-1]["ref_low"] is not None and v[-1]["ref_high"] is not None), "nf": sum(1 for m in M if m["flag"]),
            "cy": S["counts"]["cycles"], "sy": S["counts"]["symptom_entries"], "dx": sum(1 for h in S["history"] if h["kind"] == "diagnosis"), "rec": sum(1 for x in Q["categories"] if x["n"]), "tot": len(Q["categories"]), "ni": len(Q["issues"]),
            "none": not (len(M) + S["counts"]["cycles"] + S["counts"]["symptom_entries"] + S["counts"]["documents"] + len(S["streams"]) + len(S["history"])), "nv": len(await api(pg, f"/api/patients/{pid}/twin")), "np": len(await api(pg, f"/api/patients/{pid}/predictions")), "cs": CS}
def expected(c):
    e = []; s1 = lambda n, a, b: a if n == 1 else b
    if c["none"]: e.append((0, "Nothing has been recorded for this patient yet"))
    else: e += [(0, f"{c['vm']} verified {s1(c['vm'], 'measurement', 'measurements')}"), (0, f"{c['cy']} recorded period {s1(c['cy'], 'start', 'starts')}"), (0, f"{c['sy']} patient-reported symptom {s1(c['sy'], 'entry', 'entries')}")]
    if c["dx"]: e.append((0, f"{c['dx']} diagnosis {s1(c['dx'], 'entry appears', 'entries appear')} in the recorded history"))
    if c["nt"]:
        e.append((1, f"{c['nt']} {s1(c['nt'], 'test is', 'different tests are')} on record"))
        if c["ns"]: e.append((1, f"{c['ns']} {s1(c['ns'], 'test has a single result and shows one point', 'tests have a single result and show one point')}"))
        if c["nr"]: e.append((1, f"The shaded {s1(c['nr'], 'band', 'bands')} on {'it' if c['nt'] == 1 else str(c['nr']) + ' of them'} {s1(c['nr'], 'is', 'are')} the laboratory’s own {s1(c['nr'], 'reference range, not a diagnostic threshold', 'reference ranges, not diagnostic thresholds')}"))
        if c["nf"]: e.append((1, f"{c['nf']} {s1(c['nf'], 'result carries', 'results carry')} a flag"))
    cs = c["cs"]; n = cs["n_starts"]; L = cs["lengths"]
    if n == 1: e.append((2, "1 period start is recorded"))
    elif n > 1:
        e.append((2, f"{n} period starts are recorded"))
        if len(L) == 1: e += [(2, "Cycle length is derived"), (2, f"1 here, {cs['min']} days.")]
        else: e += [(2, "Cycle lengths are derived"), (2, f"{len(L)} here, all {cs['min']} days." if cs["min"] == cs["max"] else f"{len(L)} here, from {cs['min']} to {cs['max']} days (median {cs['median']:g})")]
    e.append((3, f"{c['rec']} of {c['tot']} data categories {s1(c['rec'], 'has', 'have')} records")); e.append((3, f"{c['ni']} {s1(c['ni'], 'issue was', 'issues were')} found" if c["ni"] else "found no issues"))
    if c["nv"]: e.append((4, f"{c['nv']} {s1(c['nv'], 'version is', 'versions are')} saved"))
    if c["np"]: e.append((5, f"{c['np']} stored {s1(c['np'], 'prediction', 'predictions')}"))
    e.append((6, f"{c['vm']} {s1(c['vm'], 'measurement', 'measurements')}, {c['cy']} period {s1(c['cy'], 'start', 'starts')}, {c['sy']} symptom {s1(c['sy'], 'entry', 'entries')}"))
    return e
NOUNS = [("diagnosis entry", "diagnosis entries"), ("verified measurement", "verified measurements"), ("recorded period start", "recorded period starts"), ("patient-reported symptom entry", "patient-reported symptom entries"), ("stored prediction", "stored predictions"), ("version", "versions"), ("issue", "issues"), ("test", "tests"), ("band", "bands")]
VERBS = ["entry appear ", "entries appears", "test has a single result and show ", "tests have a single result and shows", "result carry", "results carries", "issue were", "issues was", "version are saved", "versions is saved"]
def grammar(label, T):
    miss = []; text = [t for t in T]
    bad = [f"{s}/{p}" for t in text for s, p in NOUNS if re.search(rf"\b1 {p}\b", t) or re.search(rf"\b(?:0|[2-9]|\d\d+) {s}\b", t)] + [v for t in text for v in VERBS if v in t]
    check(not bad, f"{label}: no singular/plural or verb-agreement error in any step ({bad[:3]})")
def phrases(label, T, c):
    miss = [p for i, p in expected(c) if p not in T[i]]; check(not miss, f"{label}: all {len(expected(c))} count-generated phrases render in the correct singular/plural form {miss[:2]}"); grammar(label, T)
async def launcher(pg, base, tag):
    exp = {"SYN-000": ("Ananya Rao", ["See what is new", "Open the Digital Twin", "Doctor brief"]), "SYN-001": ("Meera Sharma", ["See what is new", "Open the Digital Twin", "Doctor brief"]), "SYN-002": ("Kavya Reddy", ["Upload a report", "Add a measurement", "Record a period"])}
    ana = await api(pg, "/api/patients/SYN-000/summary"); k = ana["counts"]
    for pid, (nm, others) in exp.items():
        await pg.goto(base + "#/workspace"); await pg.wait_for_selector("text=Choose a patient"); await pg.goto(base + f"#/p/{pid}/overview"); await pg.wait_for_selector(f".pbar .big:has-text('{nm}')"); await pg.wait_for_selector(".ph .ph3 button[data-act=tourStart]")
        labels = await pg.eval_on_selector_all(".ph .ph3 > div:last-child .btn", "e=>e.map(x=>x.innerText.trim())"); btn = pg.locator(".ph button[data-act=tourStart]"); box = await btn.bounding_box()
        sib = await pg.evaluate("()=>document.querySelector('.ph .ph3 > div:last-child a.btn').className"); mine = await pg.evaluate("()=>document.querySelector('.ph button[data-act=tourStart]').className")
        check(labels == others + ["Explore a patient journey"], f"{tag} {nm}'s overview: next-actions are exactly {others} + launcher (layout otherwise unchanged) {labels}")
        check(await btn.is_visible() and box and box["x"] >= 0 and box["x"] + box["width"] <= 1280 and box["y"] + box["height"] <= 900 and sib == mine, f"{tag} {nm}: launcher is visible, inside the viewport and styled like its sibling buttons ('{mine}')")
        await btn.click(); c = await card(pg); await pg.wait_for_selector(".pbar .big:has-text('Ananya Rao')"); await pg.wait_for_timeout(500); body = await pg.inner_text("#tab"); bar = await pg.inner_text(".pbar .big")
        check(pg.url.endswith("#/p/SYN-000/overview") and "Ananya Rao" in bar and "SYN-000" in await pg.inner_text(".pbar"), f"{tag} click from {nm}: screen is Ananya's overview")
        rest = c["full"]
        if pid != "SYN-000":
            check(f"You were viewing {nm} ({pid})" in rest and "The tour always follows Ananya Rao (SYN-000)" in rest and "has switched to her record" in rest, f"{tag} click from {nm}: step 1 names who was being viewed and says the tour switched to Ananya")
            rest = rest.replace(rest[rest.index("You were viewing"):rest.index("has switched to her record") + len("has switched to her record.")], "")
        else: check("You were viewing" not in rest, f"{tag} click from Ananya: no 'switched' note (nothing switched)")
        others_ = [n for n in ("Meera", "Kavya", "SYN-001", "SYN-002") if n in rest or n in body or n in bar]
        check(not others_ and f"{k['verified_measurements']} verified measurements" in rest, f"{tag} click from {nm}: statements and screen are Ananya's only (no {others_}; her {k['verified_measurements']} verified measurements stated)")
        await pg.click("#tour [data-act=tourNext]"); c2 = await card(pg); check("You were viewing" not in c2["full"] and pg.url.endswith("/p/SYN-000/tests"), f"{tag} {nm}: note shown on step 1 only; step 2 is Ananya's Lab tests")
        await pg.click("#tour [data-act=tourExit]")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        # ===== (1) launcher, full app, real clicks =====
        await launcher(pg, B, "[full app]")
        # ===== (2) grammar from the REAL tour cards: Ananya, base state =====
        await pg.goto(B + "#/workspace"); await pg.click("button:has-text('Explore a patient journey')"); T = [c["full"] for c in await cards(pg)]; c0 = await counts(pg, "SYN-000")
        check(c0["dx"] == 1 and c0["ns"] == 3 and c0["nv"] == 1, f"fixture: Ananya base has 1 diagnosis entry, 3 single-result tests, 1 version ({ {k: c0[k] for k in ('dx','ns','nv')} })")
        check("1 diagnosis entry appears in the recorded history" in T[0] and "3 tests have a single result and show one point" in T[1] and "1 version is saved" in T[4], "[Ananya base] rendered card: '1 diagnosis entry appears', '3 tests have … show', '1 version is saved'")
        phrases("[Ananya base, tour cards]", T, c0)
        check("blocked" in T[3] and "no verified HbA1c recorded" in T[3] and "read-only" not in T[3], "[full app] Data quality, BLOCKED: actual missing requirement named, no preview wording")
        await pg.click("#tour [data-act=tourExit]")
        # ===== (2) grammar from Meera's real data, built up state by state =====
        meera = lambda: pg.evaluate(BUILD, ["SYN-001", False]); sq = sqlite3.connect(f"{tmp}/t.db")
        async def state(label):
            T = [strip(x) for x in await meera()]; c = await counts(pg, "SYN-001"); phrases(f"[Meera: {label}]", T, c); return T, c
        T, c = await state("base: 1 test, 1 result"); check("1 test is on record (Total Testosterone)" in T[1] and "The shaded band on it is the laboratory’s own reference range, not a diagnostic threshold" in T[1] and "1 test has a single result and shows one point" in T[1] and "1 of" in T[3] and "has records" in T[3], "[Meera base] 'is on record', 'band on it is', 'has a single result and shows', '1 of N … has records'")
        sq.execute("INSERT INTO history(patient,kind,text,date,provenance) VALUES('SYN-001','diagnosis','Fictional diagnosis A','2025-01-01','clinician-confirmed')"); sq.commit(); T, c = await state("1 diagnosis"); check("1 diagnosis entry appears in the recorded history" in T[0], "[Meera] singular '1 diagnosis entry appears'")
        sq.execute("INSERT INTO history(patient,kind,text,date,provenance) VALUES('SYN-001','diagnosis','Fictional diagnosis B','2025-02-01','clinician-confirmed')"); sq.commit(); T, c = await state("2 diagnoses"); check("2 diagnosis entries appear in the recorded history" in T[0], "[Meera] plural '2 diagnosis entries appear'")
        M = lambda t, v, u, d, **x: post(pg, "/api/patients/SYN-001/measurements", {"test": t, "value": v, "unit": u, "date": d, **x})
        await M("Total Testosterone", 51, "ng/dL", "2026-02-02"); T, c = await state("duplicate result → 1 issue, 1 version"); check("1 issue was found" in T[3] and "1 version is saved" in T[4] and "1 test is on record" in T[1], "[Meera] '1 issue was found', '1 version is saved'")
        await M("Total Testosterone", 0.5, "nmol/L", "2026-02-03"); T, c = await state("unit conflict → 2 issues, 2 versions"); check("2 issues were found" in T[3] and "2 versions are saved" in T[4], "[Meera] '2 issues were found', '2 versions are saved'")
        await M("Prolactin", 11, "ng/mL", "2026-02-05", ref_low=4, ref_high=23); T, c = await state("2 tests, 1 single, 1 band"); check("2 different tests are on record" in T[1] and "1 test has a single result and shows one point" in T[1] and "The shaded band on 1 of them is" in T[1], "[Meera] '2 different tests are', '1 test has … shows', 'band on 1 of them is'")
        await M("Free Testosterone", 8, "pg/mL", "2026-02-06"); await M("DHEAS", 200, "ug/dL", "2026-02-07", ref_low=65, ref_high=380); sq.execute("UPDATE measurements SET flag='Low' WHERE patient='SYN-001' AND test='DHEAS'"); sq.commit(); T, c = await state("4 tests, 3 single, 2 bands, 1 flag")
        check("4 different tests are on record" in T[1] and "3 tests have a single result and show one point" in T[1] and "The shaded bands on 2 of them are the laboratory’s own reference ranges, not diagnostic thresholds" in T[1] and "1 result carries a flag" in T[1], "[Meera] plural tests/bands/'show one point', and '1 result carries a flag'")
        await M("Cortisol", 12, "ug/dL", "2026-02-08"); sq.execute("UPDATE measurements SET flag='High' WHERE patient='SYN-001' AND test='Cortisol'"); sq.commit(); T, c = await state("2 flags"); check("2 results carry a flag" in T[1], "[Meera] '2 results carry a flag'")
        S = lambda d: post(pg, "/api/patients/SYN-001/cycles", {"start": d}); await S("2026-01-01"); T, c = await state("1 period start"); check("1 period start is recorded" in T[2] and "1 recorded period start," in T[0], "[Meera] '1 period start is recorded'")
        await S("2026-02-12"); T, c = await state("2 starts, 1 length"); check("2 period starts are recorded" in T[2] and "Cycle length is derived — only between two recorded starts: 1 here, 42 days." in T[2], "[Meera] '2 period starts are recorded', 'Cycle length is derived … 1 here, 42 days.'")
        await S("2026-03-26"); await S("2026-05-07"); T, c = await state("4 starts, 3 equal lengths"); check("Cycle lengths are derived — only between two recorded starts: 3 here, all 42 days." in T[2] and "dashed calendar cell" in T[2], "[Meera] 'Cycle lengths are derived … 3 here, all 42 days.'")
        Y = lambda d, v: post(pg, "/api/patients/SYN-001/symptoms", {"date": d, "kind": "acne", "severity": v}); await Y("2026-05-01", 2); T, c = await state("1 symptom"); check("1 patient-reported symptom entry" in T[0], "[Meera] '1 patient-reported symptom entry'")
        await Y("2026-05-02", 3); T, c = await state("2 symptoms"); check("2 patient-reported symptom entries" in T[0], "[Meera] '2 patient-reported symptom entries'")
        # ===== (3) Ananya after the demo workflow: ready state; singular result/ plural predictions =====
        wf = await pg.evaluate("""async()=>{const J=(p,o)=>fetch(p,{credentials:'same-origin',...o}).then(r=>r.json()),H={'Content-Type':'application/json'};
 const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());const d=await J('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',body:png});
 for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await J('/api/fields/'+f.id,{method:'POST',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
 const pr=await J('/api/patients/SYN-000/predict',{method:'POST',headers:H,body:JSON.stringify({as_of:'2026-03-10T23:55:00Z'})});
 const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await J('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',body:csv});
 await J('/api/patients/SYN-000/twin/update',{method:'POST',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})});await J('/api/predictions/'+pr.id+'/outcome',{method:'POST'});return pr.available}""")
        check(wf is True, "workflow ran through the real API (prediction available)")
        await pg.goto(B + "#/workspace"); await pg.click("button:has-text('Explore a patient journey')"); T = [c["full"] for c in await cards(pg)]; c1 = await counts(pg, "SYN-000")
        check(c1["ns"] == 1 and c1["np"] == 1 and c1["nv"] == 3, f"fixture: Ananya after workflow has 1 single-result test, 1 stored prediction, 3 versions")
        check("1 test has a single result and shows one point" in T[1] and "1 stored prediction;" in T[5] and "3 versions are saved" in T[4], "[Ananya workflow] rendered card: '1 test has a single result and shows one point', '1 stored prediction', '3 versions are saved'")
        phrases("[Ananya workflow, tour cards]", T, c1)
        check("all required inputs are present" in T[3] and "read-only" not in T[3] and "blocked" not in T[3], "[full app] Data quality, READY: inputs present, and no preview wording (a new prediction can run here)")
        await pg.click("#tour [data-act=tourExit]"); await post(pg, "/api/patients/SYN-000/predict", {}); await pg.goto(B + "#/workspace"); await pg.click("button:has-text('Explore a patient journey')"); T = [c["full"] for c in await cards(pg)]; c2 = await counts(pg, "SYN-000")
        check(c2["np"] == 2 and "2 stored predictions; the latest" in T[5], "[Ananya] plural '2 stored predictions; the latest …' after a second stored prediction"); phrases("[Ananya 2 predictions]", T, c2); await pg.click("#tour [data-act=tourExit]")
        check(not errs, f"no JavaScript errors in the full-app run {errs[:2]}")
        # ===== preview =====
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; pv.on("pageerror", lambda e: perr.append(str(e))); await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient")
        await launcher(pv, PV, "[preview]")
        await pv.goto(PV + "#/workspace"); await pv.click("button:has-text('Explore a patient journey')"); T = [c["full"] for c in await cards(pv)]; cp = await counts(pv, "SYN-000")
        check("1 diagnosis entry appears in the recorded history" in T[0] and "1 test has a single result and shows one point" in T[1] and "3 versions are saved" in T[4], "[preview] rendered card: '1 diagnosis entry appears', '1 test has … shows one point', '3 versions are saved'"); phrases("[preview Ananya, tour cards]", T, cp)
        check("all required inputs are present" in T[3] and "This read-only preview cannot run a new prediction." in T[3] and "blocked" not in T[3] and "was run" not in T[3], "[preview] Data quality, READY: states BOTH that inputs are present AND that this read-only preview cannot run a new prediction")
        for pid, nm in (("SYN-001", "Meera Sharma"), ("SYN-002", "Kavya Reddy")):
            t4 = strip((await pv.evaluate(BUILD, [pid, True]))[3]); r = (await api(pv, f"/api/patients/{pid}/quality"))["prediction"]["reasons"]
            check("blocked" in t4 and all(x in t4 for x in r) and "This read-only preview cannot run a new prediction." in t4 and "all required inputs are present" not in t4, f"[preview {nm}] Data quality, BLOCKED: actual requirement(s) {r} named AND the read-only limitation stated")
            tf = strip((await pv.evaluate(BUILD, [pid, False]))[3]); check("read-only" not in tf, f"[full-app wording for {nm}] no preview limitation in the full-app variant")
        for pid in ("SYN-001", "SYN-002"):
            Tp = [strip(x) for x in await pv.evaluate(BUILD, [pid, True])]; phrases(f"[preview {pid}]", Tp, await counts(pv, pid))
        check(not perr, f"no JavaScript errors in the preview run {perr[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL CLOSE-OUT CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
