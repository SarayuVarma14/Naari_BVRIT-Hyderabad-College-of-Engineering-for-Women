"""Guided demo (evaluator workflow): 10 steps on the existing tour engine. Live app + static preview, Chromium. Run: python3 tests/test_demo_flow.py
Checks: steps/routes/titles; the demo never performs an action for the user; text is built from the patient's real records and updates after the user really uploads / approves / imports
(counts equal the API each time); Back/Next/Finish/Exit; pause + Return when the user leaves; patient switching cannot change whom the demo follows; the Doctor Brief shown is the followed patient's;
no false HbA1c claim; preview states which steps need the full application, makes no write request, and shows the same facts as the full app."""
import os, sys, re, json, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
flat = lambda t: re.sub(r"\s+", " ", t).strip()
TITLES = ["1 · Choose a patient and review their history", "2 · Upload a clinical report", "3 · Review the extracted values", "4 · Approve results or leave them pending", "5 · Import wearable data and check its quality",
          "6 · Update or build the Digital Twin", "7 · Examine the new version and compare it", "8 · What the prediction measures — and what it does not", "9 · Generate the Doctor Brief", "10 · How the brief summarises the evidence"]
TABS = ["overview", "reports", "reports", "reports", "physio", "twin", "twin", "predict", "export", "export"]
async def api(pg, p): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", p)
async def card(pg):
    await pg.wait_for_function("()=>{const t=document.querySelector('#tour');return t&&!/Loading/.test(t.innerText)}", timeout=15000); await pg.wait_for_timeout(250)
    return await pg.evaluate("()=>({small:document.querySelector('#tour small').innerText,h3:(document.querySelector('#tour h3')||{}).innerText,full:document.querySelector('#tour').innerText.replace(/\\s+/g,' '),hash:location.hash,next:(document.querySelector('#tour [data-act=tourNext]')||{}).innerText})")
async def to_step(pg, n):
    c = await card(pg)
    while True:
        cur = int(re.search(r"Step (\d+) of", c["small"])[1])
        if cur == n: return c
        await pg.click("#tour [data-act=" + ("tourNext" if cur < n else "tourBack") + "]"); c = await card(pg)
async def start(pg, where="workspace"):
    await pg.evaluate("h=>{location.hash=h}", "#/workspace"); await pg.wait_for_selector("#pl a.card"); await pg.click("[data-act=demoStart]"); return await card(pg)
async def texts(pg, prev):
    return await pg.evaluate("""async prev=>{const sum=await api('/api/patients/SYN-000/summary'),o=[];for(const s of TFLOW){o.push((await s.text({pid:'SYN-000',sum,name:'Ananya Rao',prev})).replace(/<span class="cap">.*?<\\/span>/g,''))}return o}""", prev)
async def live(b):
    pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
    c = await start(pg); check("Step 1 of 10" in c["small"] and "Ananya Rao" in c["full"] and c["hash"].endswith("/p/SYN-000/overview") and c["h3"] == TITLES[0], "[full app] 'Guided demo' starts at step 1 of 10 on Ananya's overview")
    check(await pg.locator("#tour [data-act=tourBack]").is_disabled() and (await pg.inner_text("#tour [data-act=tourExit]")) == "Exit tour", "[full app] Back disabled on step 1; Exit tour present")
    # walk the whole script at the BASE state, recording what was claimed; nothing may be changed by the tour
    k0 = (await api(pg, "/api/patients/SYN-000/summary"))["counts"]; v0 = len(await api(pg, "/api/patients/SYN-000/twin")); d0 = len(await api(pg, "/api/patients/SYN-000/documents")); base = {}
    for i in range(1, 11):
        c = await to_step(pg, i); base[i] = c["full"]
        check(c["h3"] == TITLES[i - 1] and c["hash"].startswith(f"#/p/SYN-000/{TABS[i - 1]}") and "Following Ananya Rao (SYN-000)" in c["full"], f"[full app] step {i}: title, real route {c['hash']}, names the followed patient")
    check((await card(pg))["next"] == "Finish", "[full app] last step offers Finish")
    k1 = (await api(pg, "/api/patients/SYN-000/summary"))["counts"]; check(k1 == k0 and len(await api(pg, "/api/patients/SYN-000/twin")) == v0 and len(await api(pg, "/api/patients/SYN-000/documents")) == d0, "[full app] walking all 10 steps changed nothing: the demo never uploads, approves, imports or updates by itself")
    check("Your turn" in base[2] and "will not upload for you" in base[2] and "Your turn" in base[5] and "Your turn" in base[6] and "no report has been uploaded yet" in base[2], "[full app] action steps pause and tell the user to use the real control ('Your turn'), saying the tour will not do it")
    check("no report is on file" in base[3] and "nothing to approve yet" in base[4], "[full app] base state: review/approve steps say there is nothing yet instead of claiming results")
    check("HbA1c is not recorded" in base[8] or "no verified HbA1c recorded" in base[8], "[full app] base state: prediction step does NOT claim an HbA1c exists for Ananya")
    check("above 180 mg/dL within the next 2 hours" in base[8] and "not a PCOS diagnosis" in base[8] and "synthetic" in base[8], "[full app] step 8 states the exact prediction task, synthetic training, not a diagnosis")
    # --- the evaluator really performs the workflow; the demo text follows the real results ---
    await to_step(pg, 2); await pg.click("[data-act=sampleScan]"); await pg.wait_for_selector("#tour:has-text('Tour paused')", timeout=30000); await pg.click("#tour [data-act=tourReturn]"); c = await card(pg)
    D = await api(pg, "/api/patients/SYN-000/documents"); check(len(D) == 1 and f"1 report is on file ({D[0]['filename']})" in c["full"], f"[full app] after a REAL upload, step 2 reports it: {D[0]['filename']}")
    c = await to_step(pg, 3); doc = await api(pg, f"/api/documents/{D[0]['id']}"); nref = len([f for f in doc["fields"] if f["ref_low"] is not None and f["ref_high"] is not None])
    check(c["hash"].endswith(f"/reports/{D[0]['id']}") and f"has {len(doc['fields'])} extracted rows" in c["full"] and f"{nref} rows have a printed reference range" in c["full"], f"[full app] step 3 opens the review page of the uploaded report; {len(doc['fields'])} rows, {nref} with a printed range = API")
    c = await to_step(pg, 4); D = await api(pg, "/api/patients/SYN-000/documents"); check(f"{D[0]['pending']} values are pending and {D[0]['approved']} are approved" in c["full"] and f"Approve all pending ({D[0]['pending']})" in c["full"], f"[full app] step 4: {D[0]['pending']} pending / {D[0]['approved']} approved = API")
    check(len((await api(pg, "/api/patients/SYN-000/summary"))["counts"].keys()) and (await api(pg, "/api/patients/SYN-000/summary"))["counts"]["verified_measurements"] == k0["verified_measurements"], "[full app] pending values are not counted as verified")
    # a real reviewer corrects the OCR-misread test name before approving (the page says names are never guessed)
    await pg.evaluate("()=>{document.querySelectorAll('#ft tr[data-f] input[name=name]').forEach(i=>{if(/^hba/i.test(i.value))i.value='HbA1c'})}")
    await pg.click("[data-act=apprAll]"); await pg.wait_for_timeout(2500); await to_step(pg, 3); c = await to_step(pg, 4); D = await api(pg, "/api/patients/SYN-000/documents")
    check(D[0]["pending"] == 0 and f"{D[0]['approved']} are approved" in c["full"] and "0 values are pending" in c["full"] and "Nothing is pending" in c["full"], f"[full app] after approving through the real button, step 4 shows 0 pending / {D[0]['approved']} approved")
    S0 = await api(pg, "/api/patients/SYN-000/summary"); check(S0["counts"]["verified_measurements"] == k0["verified_measurements"] + D[0]["approved"], f"[full app] verified measurements rose from {k0['verified_measurements']} to {S0['counts']['verified_measurements']}")
    c = await to_step(pg, 5); g0 = next(x["n"] for x in S0["streams"] if x["metric"] == "glucose"); check(re.search(rf"glucose[^;,]* {g0:,} readings", c["full"], re.I) is not None and "validation preview" in c["full"] and "glucose gaps longer than 15 minutes" in c["full"], f"[full app] step 5 lists the real streams (glucose {g0:,}) and the validation-preview contents")
    await pg.click("[data-act=sampleTs]"); await pg.wait_for_selector("#tsprev .note", timeout=15000); prev = flat(await pg.inner_text("#tsprev")); check("nothing is stored yet" in prev.lower() and "out of range (flagged, excluded)" in prev.lower() and "invalid timestamps" in prev.lower() and "glucose gaps" in prev.lower(), "[full app] the real import shows the validation preview (coverage, gaps, invalid readings, exclusions) before storing anything")
    check(len(await api(pg, "/api/patients/SYN-000/twin")) == 2, "[full app] previewing the import stored nothing (still 2 Twin versions: seed + approval)")
    await pg.click("[data-act=tsimport]"); await pg.wait_for_timeout(3000); c = await to_step(pg, 4); c = await to_step(pg, 5); S1 = await api(pg, "/api/patients/SYN-000/summary"); g1 = next(x["n"] for x in S1["streams"] if x["metric"] == "glucose")
    check(g1 > g0 and re.search(rf"glucose[^;,]* {g1:,} readings", c["full"], re.I) is not None, f"[full app] after the real import, step 5 shows glucose {g0:,} → {g1:,}")
    T = await api(pg, "/api/patients/SYN-000/twin"); c = await to_step(pg, 6); check(f"the Twin is at version {T[0]['version']}" in c["full"], f"[full app] step 6: Twin at version {T[0]['version']} = API")
    c = await to_step(pg, 7); check(f"version {T[0]['version']}" in c["full"] and f"{len(T)} versions are saved" in c["full"] and "Use Compare two versions" in c["full"], f"[full app] step 7: {len(T)} versions saved, Compare offered")
    c = await to_step(pg, 8); check("HbA1c on file: 6.1 %" in c["full"] and "HbA1c is not recorded" not in c["full"], "[full app] after the approval, the prediction step shows HbA1c 6.1 % on file (and not the old claim)")
    c = await to_step(pg, 9); k2 = S1["counts"]; check(f"{k2['verified_measurements']} measurements" in c["full"], f"[full app] step 9: brief covers {k2['verified_measurements']} measurements = API")
    Br = await api(pg, "/api/patients/SYN-000/brief"); R = {r["key"]: len(r["items"]) for r in Br["review_items"]}; c = await to_step(pg, 10)
    check(f"{R['not_recorded']} “not recorded” item" in c["full"] and f"{R['awaiting_review']} awaiting review" in c["full"] and "never presented as normal" in c["full"] and "not a diagnosis" in c["full"], f"[full app] step 10: missing/pending counts equal the brief's own review items {R}")
    # patient switching and the Doctor Brief
    c = await to_step(pg, 9); await pg.select_option(".psel select", "SYN-001"); await pg.wait_for_selector("#tour:has-text('Tour paused')", timeout=10000); pc = await card(pg)
    check("Ananya Rao" in pc["full"] and "step 9" in pc["full"] and "Return to step 9" in pc["full"], "[full app] switching patient pauses the demo; it still says it follows Ananya and offers 'Return to step 9'")
    await pg.click("#tour [data-act=tourReturn]"); c = await card(pg); head = flat(await pg.inner_text(".pbar")).split("Kavya Reddy (SYN-002)")[-1]; page = flat(await pg.inner_text("#tab"))
    check("Step 9 of 10" in c["small"] and c["hash"].endswith("/p/SYN-000/export") and head.strip().startswith("Ananya Rao") and "Ananya Rao" in page and "Meera Sharma" not in page, "[full app] Return restores step 9 on Ananya's Doctor Brief; no Meera content on the page")
    await pg.click("#tour [data-act=tourNext]"); c = await card(pg); await pg.click("#tour [data-act=tourNext]"); await pg.wait_for_timeout(400); check(await pg.locator("#tour").count() == 0, "[full app] Finish closes the demo")
    c = await start(pg); await pg.click("#tour [data-act=tourNext]"); await card(pg); await pg.click("#tour [data-act=tourExit]"); await pg.wait_for_timeout(300); check(await pg.locator("#tour").count() == 0, "[full app] Exit tour closes it from any step")
    # a different patient's page: starting the demo there says so, then follows Ananya
    await pg.evaluate("location.hash='#/p/SYN-002/overview'"); await pg.wait_for_timeout(900); await pg.evaluate("ACT.demoStart()"); c = await card(pg); check("You were viewing Kavya Reddy (SYN-002)" in c["full"] and c["hash"].endswith("/p/SYN-000/overview"), "[full app] starting from Kavya's page: says what you were viewing, then follows Ananya"); await pg.click("#tour [data-act=tourExit]")
    check(not errs, f"[full app] no JavaScript errors {errs[:2]}"); return await texts(pg, False)
async def preview(b, full_texts):
    pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; writes = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("request", lambda r: writes.append(r.method + " " + r.url) if r.method == "POST" and not re.search(r"/api/(demo-login|logout)$", r.url) else None)
    await pg.goto(PV); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient"); c = await start(pg); allc = {}
    check("Step 1 of 10" in c["small"], "[preview] guided demo starts")
    for i in range(1, 11):
        c = await to_step(pg, i); allc[i] = c["full"]; check(c["h3"] == TITLES[i - 1] and c["hash"].startswith(f"#/p/SYN-000/{TABS[i - 1]}"), f"[preview] step {i}: title and route {c['hash']}")
    check(all("Your turn" not in allc[i] for i in allc) and "cannot upload" in allc[2] and "cannot approve or reject" in allc[4] and "cannot import" in allc[5] and "cannot build or update a Twin" in allc[6], "[preview] steps 2, 4, 5, 6 say they need the full application; no step tells the viewer to do something the preview cannot do")
    check("5 extracted rows" in allc[3] and "0 values are pending and 5 are approved" in allc[4], "[preview] the embedded snapshot illustrates the completed upload/review stage (5 rows, all approved) without presenting it as done in this session")
    now = await texts(pg, True)
    def norm(i, s):   # the preview fixture also contains one stored prediction + outcome that this test's live workflow did not create; compare everything else
        s = s.replace("3,456", "3456")
        if i == 8: s = re.search(r"Inputs for a <b>new</b> prediction:.*", s)[0]
        if i == 10: s = re.sub(r"\d+ stored predictions?", "N stored predictions", s)
        return s
    diffs = [i + 1 for i, (a, b2) in enumerate(zip(full_texts, now)) if norm(i + 1, a) != norm(i + 1, b2)]
    check(not diffs, f"[preview] patient facts in all 10 steps equal the full application after the same workflow (differing steps: {diffs})")
    check(not writes, f"[preview] no write request {writes[:2]}"); check(not errs, f"[preview] no JavaScript errors {errs[:2]}")
    await pg.click("#tour [data-act=tourExit]"); await pg.wait_for_timeout(200); check(await pg.locator("#tour").count() == 0, "[preview] Exit closes the demo")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); ft = await live(b); subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL); await preview(b, ft); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL DEMO-FLOW CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
