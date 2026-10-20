"""Fix 2: guided-tour consistency. Run: python3 tests/test_tour_states.py
Compares every tour sentence with (a) live API data, (b) the page the tour is showing, in both Ananya states, for Meera/Kavya, under navigation/failure/delay, and full app vs static preview."""
import os, sys, re, json, html, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
strip = lambda x: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
async def api(pg, p): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", p)
async def card(pg):
    await pg.wait_for_selector("#tour p:not(:has-text('Loading'))", timeout=20000)
    full = await pg.inner_text("#tour p")
    nocap = await pg.evaluate("()=>{const p=document.querySelector('#tour p').cloneNode(true);p.querySelectorAll('.cap').forEach(e=>e.remove());return p.textContent}")
    cap = await pg.evaluate("()=>[...document.querySelectorAll('#tour p .cap')].map(e=>e.textContent).join(' ')")
    return {"h3": await pg.inner_text("#tour h3"), "full": re.sub(r"\s+", " ", full).strip(), "nocap": re.sub(r"\s+", " ", nocap).strip(), "cap": cap, "small": await pg.inner_text("#tour small:first-child"), "follow": await pg.evaluate("()=>{const s=document.querySelectorAll('#tour small');return s[1]?s[1].textContent:''}")}
DOM_JS = """(i)=>{const T=document.querySelector('#tab'),q=s=>[...T.querySelectorAll(s)],card=p=>q('.card').find(c=>c.querySelector('h3')&&c.querySelector('h3').innerText.trim().startsWith(p)),o={};
 if(i==2){const sv=q('svg[role=img]'),bp=sv.find(s=>s.getAttribute('aria-label')=='Blood pressure');o.nonbp=sv.filter(s=>s!==bp).length;o.bpseries=bp?bp.closest('.card').querySelectorAll('.legend span').length:0;o.bands=sv.filter(s=>s.querySelector('rect[fill="#A9BDAA"]')).length;o.flags=q('table tr').filter(r=>r.cells.length==5&&r.cells[0].tagName=='TD'&&r.cells[3].innerText.trim()!='').length;o.hb=sv.some(s=>/hba1c/i.test(s.getAttribute('aria-label')||''));o.empty=T.innerText.includes('No verified measurements')}
 if(i==3){o.est=q('.cal .est').length;const c=card('Recorded periods');o.rows=c?[...c.querySelectorAll('tr')].filter(r=>r.cells[0].tagName=='TD').length:0}
 if(i==4){const c=card('1 ·');o.total=c?[...c.querySelectorAll('tr')].filter(r=>r.cells[0].tagName=='TD').length:0;o.recorded=c?[...c.querySelectorAll('tr')].filter(r=>r.cells[0].tagName=='TD'&&r.cells[1].innerText.trim()=='Recorded').length:0;const k=card('4 ·');o.issues=k?k.querySelectorAll('.jit').length:0;o.yes=T.innerText.includes('Yes — all required inputs are present')}
 if(i==5){const c=card('Version history');o.versions=c?c.querySelectorAll('.ev').length:0;o.compare=!!T.querySelector('#ca')}
 if(i==6){o.ready=T.innerText.includes('Inputs sufficient to run');o.unav=T.innerText.includes('Prediction unavailable');const c=card('Prediction history');o.hist=c?[...c.querySelectorAll('tr')].filter(r=>r.cells[0].tagName=='TD').length:0}
 if(i==7){o.print=!!T.querySelector('[data-act=briefPrint]')}
 return o}"""
def consist(i, txt, d, label):
    """tour sentence <-> visible page"""
    g = lambda rx: (re.search(rx, txt) or [None, None])[1]
    if i == 2:
        n = g(r"(\d+) (?:test is|different tests are) on record")
        if d["empty"]: check("no verified measurements" in txt, f"{label} step 2: empty page ⇔ 'no verified measurements' claim")
        else:
            check(n is not None and int(n) == d["nonbp"] + d["bpseries"], f"{label} step 2: claimed {n} tests = {d['nonbp']} test charts + {d['bpseries']} blood-pressure series on the page")
            mb = re.search(r"bands? on (?:(\d+) of them|it) ", txt); b = (mb[1] or "1") if mb else None; check(int(b or 0) == d["bands"], f"{label} step 2: claimed reference bands ({b or 0}) = bands drawn ({d['bands']})")
            f = g(r"(\d+) results? carr(?:y|ies) a flag"); check(int(f or 0) == d["flags"], f"{label} step 2: claimed lab-flagged results ({f or 0}) = flags shown ({d['flags']})")
            check(("HbA1c on file" in txt) == d["hb"] and (("HbA1c is not recorded" in txt) == (not d["hb"])), f"{label} step 2: HbA1c claim matches the page (page shows HbA1c: {d['hb']})")
    if i == 3:
        n = g(r"(\d+) period starts? (?:is|are) recorded")
        check((n is None and d["rows"] == 0) or (n is not None and int(n) == d["rows"]), f"{label} step 3: claimed period starts ({n}) = rows in the periods table ({d['rows']})")
        check(("dashed calendar cell" in txt) == (d["est"] == 1), f"{label} step 3: dashed-estimate claim ⇔ estimate cell on calendar ({d['est']})")
    if i == 4:
        m = re.search(r"(\d+) of (\d+) data categories", txt); check(m and int(m[1]) == d["recorded"] and int(m[2]) == d["total"], f"{label} step 4: categories claim {m and m[0]} = page ({d['recorded']} of {d['total']})")
        k = g(r"(\d+) issues? (?:was|were) found"); check(int(k or 0) == d["issues"], f"{label} step 4: claimed issues ({k or 0}) = issues listed ({d['issues']})")
        check(("all required inputs are present" in txt) == d["yes"], f"{label} step 4: prediction-input claim matches the page card ({d['yes']})")
    if i == 5:
        n = g(r"(\d+) versions? (?:is|are) saved"); check((n is None and d["versions"] == 0) or int(n) == d["versions"], f"{label} step 5: claimed versions ({n}) = history entries ({d['versions']})")
        check(("Use Compare two versions" in txt) == d["compare"], f"{label} step 5: Compare claim ⇔ Compare control on page ({d['compare']})")
    if i == 6:
        check(("all present" in txt) == d["ready"] and ("not sufficient" in txt) == d["unav"], f"{label} step 6: new-prediction readiness claim = page (ready={d['ready']}, blocked={d['unav']})")
        n = g(r"(\d+) stored predictions?"); check(int(n or 0) == d["hist"], f"{label} step 6: claimed stored predictions ({n or 0}) = rows in prediction history ({d['hist']})")
    if i == 7: check(d["print"], f"{label} step 7: Print / Save as PDF control exists on the page")
async def collect(pg, base_hash=""):
    out = {}
    for i in range(1, 8):
        c = await card(pg); await pg.wait_for_selector(".pbar", timeout=15000); await pg.wait_for_timeout(700); c["dom"] = await pg.evaluate(DOM_JS, i); c["url"] = pg.url.split("#")[1]; c["pbar"] = await pg.inner_text(".pbar .big"); out[i] = c
        if i < 7: await pg.click("#tour [data-act=tourNext]")
    return out
async def start_from_workspace(pg):
    await pg.goto(pg.url.split("#")[0] + "#/workspace"); await pg.wait_for_selector("text=Choose a patient"); await pg.click("button:has-text('Explore a patient journey')"); await card(pg)
async def goto_step(pg, n):
    for _ in range(12):
        cur = int(re.search(r"Step (\d+)", await pg.inner_text("#tour small:first-child")).group(1))
        if cur == n: return
        await pg.click("#tour [data-act=tourNext]" if cur < n else "#tour [data-act=tourBack]"); await card(pg)
BUILD = "async(pid)=>{const sum=await api(`/api/patients/${pid}/summary`);const out=[];for(const s of TSTEPS){out.push(await s.text({pid,sum,name:sum.profile.name,prev:false}))}return out}"
WORKFLOW = """async()=>{const J=(p,o)=>fetch(p,{credentials:'same-origin',...o}).then(r=>r.json()),H={'Content-Type':'application/json'};
 const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());const d=await J('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',body:png});
 for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await J('/api/fields/'+f.id,{method:'POST',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
 const pr=await J('/api/patients/SYN-000/predict',{method:'POST',headers:H,body:JSON.stringify({as_of:'2026-03-10T23:55:00Z'})});
 const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await J('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',body:csv});
 await J('/api/patients/SYN-000/twin/update',{method:'POST',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})});await J('/api/predictions/'+pr.id+'/outcome',{method:'POST'});return {fields:d.fields.length,available:pr.available}}"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        # ===== AUDIT: real state from the APIs =====
        S = {pid: await api(pg, f"/api/patients/{pid}/summary") for pid in ("SYN-000", "SYN-001", "SYN-002")}; M0 = [m for m in await api(pg, "/api/patients/SYN-000/measurements") if m["verified"]]
        check(not any(m["test"].lower() == "hba1c" for m in M0) and S["SYN-000"]["snapshot"]["version"] == 1 and (await api(pg, "/api/patients/SYN-000/predictions")) == [] and S["SYN-000"]["readiness"]["available"] is False, "AUDIT fresh full app: Ananya has NO HbA1c, Twin v1, no stored prediction, new prediction blocked")
        check(S["SYN-001"]["counts"]["verified_measurements"] == 1 and S["SYN-001"]["streams"] == [] and S["SYN-001"]["snapshot"] is None, "AUDIT: Meera has 1 verified measurement, no wearable streams, no Twin")
        check(all(S["SYN-002"]["counts"][k] == 0 for k in ("verified_measurements", "cycles", "symptom_entries", "documents")) and S["SYN-002"]["snapshot"] is None, "AUDIT: Kavya has no records at all")
        # ===== TEST 1a: Ananya, base state =====
        await pg.click("button:has-text('Explore a patient journey')"); await card(pg); A = await collect(pg)
        check(all("Ananya Rao" in A[i]["pbar"] and "Ananya Rao (SYN-000)" in A[i]["follow"] and "synthetic" in A[i]["follow"] for i in A), "every step names the patient (card + page bar) and says synthetic demo patient")
        for i in A: consist(i, A[i]["nocap"], A[i]["dom"], "[Ananya, base]")
        t = {i: A[i]["full"] for i in A}
        check("HbA1c is not recorded" in t[2] and "HbA1c on file" not in t[2], "[Ananya, base] step 2 says HbA1c is not recorded (true here)")
        check("no prediction is stored" in t[6] and "not sufficient" in t[6] and "no verified HbA1c recorded" in t[6] and "Use sample synthetic lab report (PDF)" in t[6] and "all present" not in t[6], "[Ananya, base] step 6: nothing stored, new prediction blocked by missing HbA1c, correct instruction")
        check("version 1" in t[5] and "1 version is saved" in t[5] and "Compare and Replay appear once a second version exists" in t[5], "[Ananya, base] step 5: only version 1; Compare/Replay not claimed")
        check(all(("In general" in t[6]) and ("Ananya Rao:" in t[i]) for i in (1, 2, 3, 4, 5, 6, 7)), "patient-specific statements are labelled with the patient's name; the app-wide model statement is labelled 'In general'")
        # ===== TEST 2/3: Meera and Kavya through the same step builders, cross-checked with their own pages =====
        for pid, nm in (("SYN-001", "Meera Sharma"), ("SYN-002", "Kavya Reddy")):
            T = [strip(x) for x in await pg.evaluate(BUILD, pid)]; lab = f"[{nm}]"
            check(all("Ananya" not in x and "SYN-000" not in x for x in T), f"{lab} no Ananya name/ID in any step text")
            check(nm in T[0] and "synthetic" in T[0], f"{lab} step 1 names the patient and says synthetic")
            for i, tabn in ((2, "tests"), (3, "cycle"), (4, "quality"), (5, "twin"), (6, "predict")):
                await pg.goto(B + f"#/p/{pid}/{tabn}"); await pg.wait_for_selector(".pbar"); await pg.wait_for_timeout(600); consist(i, T[i-1], await pg.evaluate(DOM_JS, i), lab)
            await pg.goto(B + f"#/p/{pid}/export"); await pg.wait_for_selector("[data-act=briefPrint]"); consist(7, T[6], await pg.evaluate(DOM_JS, 7), lab)
        TM = [strip(x) for x in await pg.evaluate(BUILD, "SYN-001")]; TK = [strip(x) for x in await pg.evaluate(BUILD, "SYN-002")]
        check("1 verified measurement" in TM[0] and "0 recorded period starts" in TM[0] and "no wearable data" in TM[0] and "1 test is on record" in TM[1] and "Total Testosterone" in TM[1] and "No periods are recorded" in TM[2] or "no periods are recorded" in TM[2], "[Meera] steps 1–3: one test, no cycles, no wearable data")
        check("has not been built yet" in TM[3] and "no glucose time-series imported" in TM[3] and "no Digital Twin version has been built yet" in TM[4] and "no prediction is stored" in TM[5] and "Approve all pending" not in TM[5] and "Open Data quality" in TM[5], "[Meera] steps 4–6: no Twin, prediction blocked by missing glucose, NO HbA1c-approval instruction")
        check("Nothing has been recorded" in TK[0] and "no verified measurements are on record" in TK[1] and "no periods are recorded" in TK[2] and "no Digital Twin version has been built yet" in TK[4] and "no prediction is stored" in TK[5], "[Kavya] every step states the empty record honestly (no fabricated values)")
        # ===== TEST 4: start the tour while viewing a different patient =====
        await pg.goto(B + "#/p/SYN-001/overview"); await pg.wait_for_selector(".pbar .big:has-text('Meera Sharma')"); await pg.evaluate("()=>ACT.tourStart()"); c = await card(pg); await pg.wait_for_selector(".pbar .big:has-text('Ananya Rao')")
        check("You were viewing Meera Sharma (SYN-001)" in c["full"] and "always follows Ananya Rao" in c["full"] and pg.url.endswith("#/p/SYN-000/overview") and "Ananya Rao (SYN-000)" in c["follow"], "starting while viewing Meera: the card explains the move to Ananya, and the screen is Ananya's")
        await pg.click("#tour [data-act=tourNext]"); c2 = await card(pg); check("You were viewing" not in c2["full"], "the 'you were viewing' note appears on step 1 only")
        # ===== TEST 5: Back / Next / Exit / Return =====
        await start_from_workspace(pg); check(await pg.locator("#tour [data-act=tourBack]").is_disabled(), "Back is disabled on step 1")
        await pg.click("#tour [data-act=tourNext]"); c = await card(pg); check("Step 2 of 7" in c["small"] and pg.url.endswith("/p/SYN-000/tests"), "Next → step 2 and its page")
        await pg.click("#tour [data-act=tourBack]"); c = await card(pg); check("Step 1 of 7" in c["small"] and pg.url.endswith("/p/SYN-000/overview"), "Back → step 1 and its page")
        await goto_step(pg, 7); check((await pg.inner_text("#tour [data-act=tourNext]")) == "Finish", "last step offers Finish"); await pg.click("#tour [data-act=tourNext]"); check(await pg.locator("#tour").count() == 0, "Finish closes the tour")
        await start_from_workspace(pg); await goto_step(pg, 3); await pg.evaluate("()=>{location.hash='#/workspace'}"); await pg.wait_for_selector("#tour:has-text('Tour paused')"); await pg.click("#tour [data-act=tourReturn]"); c = await card(pg)
        check("Step 3 of 7" in c["small"] and pg.url.endswith("/p/SYN-000/cycle"), "leave → paused → Return restores the same step and page")
        await pg.click("#tour [data-act=tourExit]"); check(await pg.locator("#tour").count() == 0, "Exit closes the tour")
        # ===== TEST 6: failed / delayed / mismatched responses =====
        await start_from_workspace(pg); await goto_step(pg, 3)
        await pg.route("**/api/patients/SYN-000/quality", lambda r: r.fulfill(status=500, body='{"error":"boom"}', content_type="application/json")); await pg.click("#tour [data-act=tourNext]"); c = await card(pg)
        check("Could not load" in c["full"] and "boom" in c["full"] and "data categories" not in c["full"] and "no issues" not in c["full"], "failed request: clear error, no stale or invented claim")
        await pg.unroute("**/api/patients/SYN-000/quality"); mq = await api(pg, "/api/patients/SYN-001/quality")
        await pg.route("**/api/patients/SYN-000/quality", lambda r: r.fulfill(status=200, body=json.dumps(mq), content_type="application/json")); await pg.click("#tour [data-act=tourBack]"); await card(pg); await pg.click("#tour [data-act=tourNext]"); c = await card(pg)
        check("different patient and was rejected" in c["full"] and "data categories" not in c["full"], "mismatched-patient response is rejected")
        await pg.unroute("**/api/patients/SYN-000/quality")
        async def slow(route): await asyncio.sleep(2.5); await route.continue_()
        await pg.route("**/api/patients/SYN-000/quality", slow); await pg.click("#tour [data-act=tourBack]"); await card(pg); await pg.click("#tour [data-act=tourNext]"); await pg.wait_for_selector("#tour p:has-text('Loading')")
        await pg.evaluate("()=>{location.hash='#/p/SYN-001/overview'}"); await pg.wait_for_selector("#tour:has-text('Tour paused')"); await pg.wait_for_timeout(3500)
        check("Tour paused" in await pg.inner_text("#tour") and "data categories" not in await pg.inner_text("#tour"), "late response after leaving the step does not overwrite the paused card")
        await pg.click("#tour [data-act=tourReturn]"); await card(pg); await goto_step(pg, 3)
        await pg.evaluate("()=>{ACT.tourNext();setTimeout(()=>ACT.tourBack(),150)}"); await pg.wait_for_timeout(3800); c = await card(pg)
        check("Step 3 of 7" in c["small"] and "period starts" in c["full"] and "data categories" not in c["full"], "a slow step-4 response arriving after Back does not overwrite step 3")
        await pg.unroute("**/api/patients/SYN-000/quality")
        await pg.route("**/api/patients/SYN-000/summary", lambda r: r.fulfill(status=500, body='{"error":"down"}', content_type="application/json")); await pg.click("#tour [data-act=tourNext]"); c = await card(pg)
        check("Could not load" in c["full"] and "down" in c["full"], "failed summary request: clear error in the tour"); await pg.unroute("**/api/patients/SYN-000/summary"); await pg.click("#tour [data-act=tourExit]")
        # ===== TEST 1b: Ananya after the demo workflow (HbA1c 6.1 % approved, prediction stored, ready) =====
        wf = await pg.evaluate(WORKFLOW); check(wf["fields"] == 5 and wf["available"], f"workflow ran through the real API (5 values approved, prediction stored) {wf}")
        P = await api(pg, "/api/patients/SYN-000/predictions"); hb = [m for m in await api(pg, "/api/patients/SYN-000/measurements") if m["verified"] and m["test"] == "HbA1c"]; Sm = await api(pg, "/api/patients/SYN-000/summary")
        check(len(hb) == 1 and hb[0]["value"] == 6.1 and hb[0]["unit"] == "%" and len(P) == 1 and P[0]["observed"] == 0 and Sm["readiness"]["available"] and Sm["snapshot"]["version"] == 3, "AUDIT after workflow: HbA1c 6.1 %, 1 stored prediction (outcome recorded), new prediction possible, Twin v3")
        await start_from_workspace(pg); C = await collect(pg); t = {i: C[i]["full"] for i in C}
        for i in C: consist(i, C[i]["nocap"], C[i]["dom"], "[Ananya, after workflow]")
        check("HbA1c on file: 6.1 % (8 Mar 2026)" in t[2] and "HbA1c is not recorded" not in t[2] and "flag" in t[2], "[Ananya, workflow] step 2 shows HbA1c 6.1 % on file (and lab flags as printed)")
        check("no verified HbA1c" not in " ".join(t.values()) and "Approve all pending" not in " ".join(t.values()) and "must approve" not in " ".join(t.values()).lower(), "[Ananya, workflow] no step says HbA1c is missing or asks to approve the report again")
        check("1 stored prediction" in t[6] and "glucose stayed at or below 180 mg/dL" in t[6] and f"{P[0]['prob']:.3f}" in t[6] and "Inputs for a new prediction: all present (HbA1c on file: 6.1 %, dated 8 Mar 2026)" in t[6] and "Choose Run prediction" in t[6], "[Ananya, workflow] step 6 separates the STORED prediction from the ability to run a NEW one")
        check("version 3" in t[5] and "3 versions are saved" in t[5] and "Use Compare two versions" in t[5] and "imported demo_new_cgm_batch.csv" in t[5], "[Ananya, workflow] step 5: version 3 with Compare/Replay")
        check("all required inputs are present" in t[4] and "blocked" not in t[4], "[Ananya, workflow] step 4: prediction inputs present")
        check(not errs, f"no JavaScript errors in the full-app run {errs[:2]}")
        # ===== TEST 8: static preview, same wording =====
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; pv.on("pageerror", lambda e: perr.append(str(e)))
        await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient"); await pv.click("button:has-text('Explore a patient journey')"); await card(pv); D = await collect(pv)
        for i in D: consist(i, D[i]["nocap"], D[i]["dom"], "[preview]")
        for i in range(1, 8): check(D[i]["nocap"] == C[i]["nocap"] and D[i]["follow"] == C[i]["follow"] and D[i]["h3"] == C[i]["h3"], f"preview step {i}: patient facts and headings identical to the full app in the same state")
        check("read-only preview cannot run a new prediction" in D[6]["cap"] and "Choose" not in D[6]["cap"] and "no server here" in D[7]["cap"] and "embedded synthetic snapshot data" in D[7]["cap"], "preview: read-only / embedded-snapshot / no-server wording; no 'Run' instruction")
        check("read-only" not in C[6]["cap"] and "Choose Run prediction" in C[6]["cap"] and "server-generated PDF" in C[7]["cap"], "full app: action sentences present, no read-only claim")
        check(all("Run prediction" not in D[i]["full"] and "Approve all" not in D[i]["full"] and "Update Digital Twin" not in D[i]["full"] for i in D), "preview never tells the viewer to run, approve or update anything")
        check(not perr, f"no JavaScript errors in the preview run {perr[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL TOUR-STATE CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
