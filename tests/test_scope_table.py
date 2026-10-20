"""Fix 4 follow-up: the Digital Twin scope table must show what is ACTUALLY present for the selected patient, not what the prototype could support.
Live app + static preview, Chromium. Run: python3 tests/test_scope_table.py
Expected cell text is derived from the API (summary, twin snapshots, quality/prediction, measurements) for each patient and compared with the rendered table."""
import os, sys, re, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
flat = lambda t: re.sub(r"\s+", " ", t).strip()
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}
async def api(pg, p): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", p)
async def table(pg, pid):
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/twin"); await pg.wait_for_selector("#tab table", timeout=15000); await pg.wait_for_timeout(400)
    rows = await pg.evaluate("()=>[...document.querySelectorAll('#tab .tw table')].find(t=>/Information/i.test(t.innerText)&&/In the saved Twin/i.test(t.innerText)) ? [...[...document.querySelectorAll('#tab .tw table')].find(t=>/In the saved Twin/i.test(t.innerText)).rows].slice(1).map(r=>[...r.cells].map(c=>c.innerText.replace(/\\s+/g,' ').trim())) : []")
    status = flat(await pg.evaluate("()=>{const t=[...document.querySelectorAll('#tab .tw table')].find(t=>/In the saved Twin/i.test(t.innerText));const u=t&&t.closest('.tw').previousElementSibling;return u?u.innerText:''}"))
    page = flat(await pg.inner_text("#tab"))
    return {r[0].split(" clinician")[0].split(" imported")[0].split(" recorded")[0].split(" patient-reported")[0].split(" unverified")[0]: r[1:] for r in rows}, status, page
async def checks(pg, label):
    for pid in ("SYN-000", "SYN-001", "SYN-002"):
        S = await api(pg, f"/api/patients/{pid}/summary"); T = await api(pg, f"/api/patients/{pid}/twin"); Q = await api(pg, f"/api/patients/{pid}/quality"); M = await api(pg, f"/api/patients/{pid}/measurements")
        rows, status, page = await table(pg, pid); k = S["counts"]; st = {x["metric"]: x["n"] for x in S["streams"]}; sn = T[0] if T else None; tag = f"[{label}] {pid}"
        vm = next(v for kk, v in rows.items() if kk.startswith("Verified clinical measurements")); gl = rows["Glucose (CGM)"]; hs = rows["Heart rate & steps"]
        check(len(rows) == 6, f"{tag}: table has 6 rows")
        check(vm[0].startswith(f"{k['verified_measurements']} ({k['verified_labs']} lab result") and f"{k['verified_vitals']} vital sign" in vm[0], f"{tag}: verified total {k['verified_measurements']} with split {k['verified_labs']}/{k['verified_vitals']} accurate")
        if not sn:
            check(all(r[1].startswith("No saved Twin yet") for r in (vm, gl)) and "none built yet" in status and "Nothing below is in a saved Twin" in status, f"{tag}: NO saved Twin → says 'No saved Twin yet'; never 'Yes' in the saved-Twin column")
            check(not any(r[1].startswith("Yes") for r in rows.values()), f"{tag}: no 'Yes' anywhere in the saved-Twin column")
        else:
            nk = len(sn["state"]["clinical"]); cg = sn["state"]["cgm"]
            check(vm[1].startswith(f"Yes — {nk} test") and f"in version {sn['version']}" in vm[1], f"{tag}: saved Twin v{sn['version']} contents: {nk} tests from the snapshot")
            check((cg.get("n") and gl[1].startswith(f"Yes — summary of {cg['n']:,} valid readings")) or (not cg.get("n") and gl[1].startswith("Not in version")), f"{tag}: glucose summary cell matches snapshot (n={cg.get('n')})")
            check(f"version {sn['version']}" in status and "none built yet" not in status, f"{tag}: status line names version {sn['version']}")
        check(hs[1].startswith("Not included in any Twin version"), f"{tag}: heart rate/steps are never claimed to be in the Twin")
        # on-record + model-input cells follow the real streams
        for key, cell, inp, nm in (("glucose", gl[0], gl[2], "glucose readings"), ("hr", hs[0], hs[2], "heart-rate readings"), ("steps", hs[0], hs[2], "step readings")):
            n = st.get(key, 0)
            if key == "glucose": check((cell.startswith(f"{n:,} readings") if n else cell == "No readings recorded") and (inp.startswith("Unavailable — no glucose readings recorded") if n == 0 else not inp.startswith("Unavailable — no glucose")), f"{tag}: glucose on record = {n}; input cell consistent")
            else:
                part = cell.split("·")[0 if key == "hr" else 1]; ip = inp.split("·")[0 if key == "hr" else 1]
                check(((f"{n:,} readings" in part) if n else "no readings recorded" in part) and ((f"no {nm} recorded" in ip) if n == 0 else "Unavailable — no " not in ip), f"{tag}: {key} on record = {n}; input cell consistent")
        hb = any(m["verified"] and re.fullmatch("hba1c", m["test"], re.I) for m in M)
        check((("a verified HbA1c is on record" in vm[2]) if hb else ("no verified HbA1c recorded" in vm[2] and "Unavailable" in vm[2])), f"{tag}: HbA1c input {'present' if hb else 'unavailable'} matches the measurements")
        pa = Q["prediction"]["available"]
        check((f"Prediction inputs: available" in status) if pa else ("Prediction inputs: unavailable" in status), f"{tag}: prediction status line matches the API (available={pa})")
        if not pa: check(all(("no verified HbA1c recorded" in status) == any("HbA1c" in r for r in Q["prediction"]["reasons"]) for _ in [0]), f"{tag}: status lists the API's own reasons")
        check(not any("Yes" in c for r in (gl, hs) for c in [r[2]] if (st.get("glucose", 0) == 0 and r is gl) or (r is hs and (st.get("hr", 0) == 0 or st.get("steps", 0) == 0) and False)), f"{tag}: zero glucose readings never shown as 'Yes'")
        for key, cell in (("cycles", rows["Menstrual cycles"]), ("symptoms", rows["Symptoms"])):
            n = k["cycles"] if key == "cycles" else k["symptom_entries"]; check((cell[0] == "None recorded") == (n == 0) and "not yet" in cell[1].lower() and cell[2] == "No", f"{tag}: {key} row ({n}) is honest about being recorded/not recorded and not a Twin feature")
        check("Cycles and symptoms are not yet twin features." in page and "research data summary" in page, f"{tag}: scope statement preserved")
    # explicit statements for the two no-Twin patients
    _, st1, p1 = await table(pg, "SYN-001"); _, st2, p2 = await table(pg, "SYN-002")
    check("Records on file: yes" in st1 and "Saved Twin: none built yet" in st1 and "Prediction inputs: unavailable" in st1 and "no glucose readings imported" in st1, f"[{label}] Meera: records exist, Twin not built, model inputs unavailable — three separate statements")
    check("Records on file: none" in st2 and "Saved Twin: none built yet" in st2 and "Prediction inputs: unavailable" in st2 and "No records yet for Kavya Reddy (SYN-002)" in p2, f"[{label}] Kavya: no records / no saved Twin / unavailable model inputs are stated separately")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        await checks(pg, "seeded state")
        # Ananya's demo workflow (report approved + glucose batch + new Twin version), then re-check every patient
        await pg.evaluate("""async()=>{const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());const d=await fetch('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',credentials:'same-origin',body:png}).then(r=>r.json());const H={'Content-Type':'application/json'};
          for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await fetch('/api/fields/'+f.id,{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
          const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await fetch('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',credentials:'same-origin',body:csv});
          await fetch('/api/patients/SYN-000/twin/update',{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})})}""")
        await checks(pg, "live app after demo workflow")
        # readings present but still no saved Twin: import the demo CSV for Meera only (live app only)
        await pg.evaluate("""async()=>{const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await fetch('/api/patients/SYN-001/timeseries?filename=demo_new_cgm_batch.csv&batch=meera-demo',{method:'POST',credentials:'same-origin',body:csv})}""")
        rows, status, page = await table(pg, "SYN-001"); gl = rows["Glucose (CGM)"]
        check(gl[0].startswith("1,152 readings") and gl[1].startswith("No saved Twin yet") and gl[2].startswith("Yes"), "Meera after importing readings (no Twin built): readings shown as on record, but the saved-Twin cell still says 'No saved Twin yet'")
        check("Prediction inputs: unavailable" in status and "no verified HbA1c recorded" in status and "Saved Twin: none built yet" in status, "Meera: readings but no HbA1c → prediction still unavailable, with the reason; Twin still not built")
        check(not errs, f"[live app] no JavaScript errors {errs[:2]}")
        # preview (built from the demo flow: Ananya v3, Meera/Kavya unchanged)
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; pv.on("pageerror", lambda e: perr.append(str(e))); await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient")
        await checks(pv, "static preview"); check("STATIC READ-ONLY PREVIEW" in await pv.inner_text(".banner"), "[preview] still read-only and synthetic"); check(not perr, f"[preview] no JavaScript errors {perr[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL SCOPE-TABLE CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
