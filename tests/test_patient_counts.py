"""Fix 4: patient identity and verified-measurement counts (API, live app, static preview). Needs playwright + chromium.
Run: python3 tests/test_patient_counts.py
Ground truth is read straight from the SQLite database (independent SQL) and, for the lab/vital split, from the Doctor Brief's own
laboratory/vital-sign classification. Checks: /api/patients and /summary agree with the database for SYN-000/001/002 in the seeded state and
after the demo workflow; unverified values are not counted; the legacy `labs` field still equals the total; every screen that shows the count
uses 'verified measurements' wording and the selected patient's own name/ID; the Twin empty states separate 'no records', 'records but no Twin'
and 'Twin exists'; the static preview shows the same numbers as its embedded API data."""
import os, sys, re, json, time, sqlite3, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
flat = lambda t: re.sub(r"\s+", " ", t)
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}
TABS = ["overview", "since", "twin", "predict", "quality", "timeline", "reports", "tests", "cycle", "symptoms", "physio", "export"]
BAD = re.compile(r"verified lab(s| value)", re.I)
async def api(pg, p): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", p)
def db_truth():
    d = sqlite3.connect(f"{tmp}/t.db"); out = {}
    for pid, nm, age in d.execute("SELECT p.id,pr.name,pr.age FROM patients p LEFT JOIN profile pr ON pr.patient=p.id ORDER BY p.id"):
        out[pid] = {"name": nm, "age": age, "verified": d.execute("SELECT COUNT(*) FROM measurements WHERE patient=? AND verified=1", (pid,)).fetchone()[0],
                    "unverified": d.execute("SELECT COUNT(*) FROM measurements WHERE patient=? AND verified=0", (pid,)).fetchone()[0]}
    return out
async def api_checks(pg, label, truth=None, expect=None):
    P = await api(pg, "/api/patients"); by = {p["id"]: p for p in P}; check(sorted(by) == ["SYN-000", "SYN-001", "SYN-002"], f"[{label}] /api/patients lists the three synthetic patients")
    for pid in sorted(by):
        p = by[pid]; S = await api(pg, f"/api/patients/{pid}/summary"); c = S["counts"]; Br = await api(pg, f"/api/patients/{pid}/brief"); M = Br["measurements"]
        check(p["name"] == NAMES[pid] == S["profile"]["name"] and S["patient"] == pid, f"[{label}] {pid}: name '{p['name']}' and ID are the same in the patient list and the summary")
        check(p["verified_measurements"] == c["verified_measurements"] and p["verified_labs"] == c["verified_labs"] and p["verified_vitals"] == c["verified_vitals"], f"[{label}] {pid}: list and summary counts agree ({p['verified_measurements']} = {p['verified_labs']} lab + {p['verified_vitals']} vital)")
        check(p["verified_labs"] + p["verified_vitals"] == p["verified_measurements"] and p["labs"] == p["verified_measurements"], f"[{label}] {pid}: lab + vital = total, and legacy 'labs' field still equals the total (backward compatible)")
        check(len(M["labs"]) == p["verified_labs"] and len(M["vitals"]) == p["verified_vitals"], f"[{label}] {pid}: lab/vital split equals the Doctor Brief's own classification ({len(M['labs'])}/{len(M['vitals'])})")
        if truth: check(truth[pid]["verified"] == p["verified_measurements"] and truth[pid]["name"] == p["name"], f"[{label}] {pid}: count and name equal an independent SQL query of the database ({truth[pid]['verified']}, {truth[pid]['name']})")
        if expect: check((p["verified_measurements"], p["verified_labs"], p["verified_vitals"]) == expect[pid], f"[{label}] {pid}: expected {expect[pid]} got {(p['verified_measurements'], p['verified_labs'], p['verified_vitals'])}")
    return by
async def ui_checks(pg, label, by):
    await pg.evaluate("location.hash='#/workspace'"); await pg.wait_for_selector("#pl a.card"); await pg.wait_for_timeout(300)
    cards = await pg.evaluate("()=>[...document.querySelectorAll('#pl a.card')].map(a=>a.innerText.replace(/\\s+/g,' '))")
    for pid, p in by.items():
        n = p["verified_measurements"]; c = next((x for x in cards if pid in x), "")
        check(NAMES[pid] in c and pid in c and f"{n} verified {'measurement' if n == 1 else 'measurements'}" in c and f"{p['verified_labs']} lab {'result' if p['verified_labs'] == 1 else 'results'} · {p['verified_vitals']} vital {'sign' if p['verified_vitals'] == 1 else 'signs'}" in c and not BAD.search(c),
              f"[{label}] workspace card {pid}: name, ID, '{n} verified measurements' and the lab/vital split")
    for pid, p in by.items():
        S = await api(pg, f"/api/patients/{pid}/summary"); n = p["verified_measurements"]
        await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/overview"); await pg.wait_for_selector("#tab .card"); await pg.wait_for_timeout(500)
        head = flat(await pg.inner_text(".pbar")).split("Kavya Reddy (SYN-002)", 1)[-1]; ov = flat(await pg.inner_text("#tab"))
        check(head.strip().startswith(NAMES[pid]) and f"{pid} · age" in head and (await pg.eval_on_selector(".psel select", "e=>e.value")) == pid, f"[{label}] {pid}: patient header (below the selector) shows '{NAMES[pid]}', the ID and the matching selection")
        if S["counts"]["verified_measurements"] + S["counts"]["documents"] + len(S["history"]) == 0 and not S["counts"]["cycles"] and not S["counts"]["symptom_entries"] and not S["streams"]:
            check(f"No records yet for {NAMES[pid]} ({pid})" in ov and "Verified measurements" not in ov, f"[{label}] {pid}: overview shows the NO RECORDS empty state naming the patient and ID (no count card)")
        else:
            check(re.search(rf"Verified measurements {n} {p['verified_labs']} lab {'result' if p['verified_labs'] == 1 else 'results'} · {p['verified_vitals']} vital", ov, re.I) is not None, f"[{label}] {pid}: overview card 'Verified measurements {n}' with the split")
        for t in TABS:
            await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/{t}"); await pg.wait_for_selector("#tab .card, #tab .empty, #tab .ph", timeout=15000); await pg.wait_for_timeout(350)
            tx = flat(await pg.inner_text("#app")); check(not BAD.search(tx), f"[{label}] {pid}/{t}: no 'verified lab values/labs' wording") if BAD.search(tx) else None
            if t == "twin":
                sc = flat(await pg.inner_text("#tab"))
                check(f"Verified clinical measurements (lab results and vital signs) clinician-verified {n} ({p['verified_labs']} lab {'result' if p['verified_labs'] == 1 else 'results'} · {p['verified_vitals']} vital {'sign' if p['verified_vitals'] == 1 else 'signs'})" in sc, f"[{label}] {pid}: Twin scope table row shows {n} verified clinical measurements with the split")
                S2 = await api(pg, f"/api/patients/{pid}/twin"); has_rec = (n + S["counts"]["cycles"] + S["counts"]["symptom_entries"] + S["counts"]["documents"] + len(S["streams"]) + len(S["history"])) > 0
                if S2: check("Version " in sc and NAMES[pid] in sc and "Records are present" not in sc and "No records yet" not in sc, f"[{label}] {pid}: Twin exists → version card with the patient's name and ID")
                elif not has_rec: check(f"No records yet for {NAMES[pid]} ({pid})" in sc and "Nothing has been recorded" in sc and "Records are present" not in sc, f"[{label}] {pid}: Twin empty state = NO RECORDS, names the patient")
                else: check(f"Records are present for {NAMES[pid]} ({pid}), but no Digital Twin version has been built" in sc and f"{n} verified measurement" in sc and "no imported glucose readings" in sc, f"[{label}] {pid}: Twin empty state = RECORDS PRESENT, NO TWIN, states the real counts and what is missing")
        else: pass
    check(True, f"[{label}] all {len(TABS)} tabs scanned for each patient (only failures are printed above)")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        # --- seeded state ---
        truth = db_truth(); by = await api_checks(pg, "seeded API", truth, {"SYN-000": (9, 5, 4), "SYN-001": (1, 1, 0), "SYN-002": (0, 0, 0)})
        check(truth["SYN-000"]["unverified"] == 0, "seeded: Ananya has no unverified measurements")
        await ui_checks(pg, "seeded app", by)
        # --- unverified values must not be counted; approving them must ---
        up = await pg.evaluate("""async()=>{const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());return fetch('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',credentials:'same-origin',body:png}).then(r=>r.json())}""")
        before = (await api(pg, "/api/patients"))[0]; check(len(up["fields"]) > 0 and before["verified_measurements"] == 9, f"uploaded report has {len(up['fields'])} pending (unverified) values and the verified count is still 9")
        wf = await pg.evaluate("""async(d)=>{const H={'Content-Type':'application/json'};for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await fetch('/api/fields/'+f.id,{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
          const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await fetch('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',credentials:'same-origin',body:csv});
          return (await fetch('/api/patients/SYN-000/twin/update',{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})}).then(r=>r.json())).version}""", up)
        truth = db_truth(); by = await api_checks(pg, "after demo workflow", truth, {"SYN-000": (14, 10, 4), "SYN-001": (1, 1, 0), "SYN-002": (0, 0, 0)})
        check(wf == 3, "workflow produced Twin version 3"); await ui_checks(pg, "live app after workflow", by)
        # --- Meera gains a Twin only if the user builds one; initial state (no Twin) was verified above. Switch patients quickly: header follows the selection ---
        for pid in ("SYN-001", "SYN-002", "SYN-000"):
            await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/overview"); await pg.wait_for_timeout(700); hd = flat(await pg.inner_text(".pbar")).split("Kavya Reddy (SYN-002)", 1)[-1]; check(hd.strip().startswith(NAMES[pid]) and not any(NAMES[o] in hd for o in NAMES if o != pid), f"switching to {pid}: header shows only {NAMES[pid]}")
        check(not errs, f"[live app] no JavaScript errors {errs[:2]}")
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; pv.on("pageerror", lambda e: perr.append(str(e))); await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient")
        pby = await api_checks(pv, "preview embedded API", None, {"SYN-000": (14, 10, 4), "SYN-001": (1, 1, 0), "SYN-002": (0, 0, 0)})
        check(all(pby[k]["verified_measurements"] == by[k]["verified_measurements"] and pby[k]["name"] == by[k]["name"] for k in by), "preview embedded data equals the live app after the same workflow")
        await ui_checks(pv, "static preview", pby); check("STATIC READ-ONLY PREVIEW" in await pv.inner_text(".banner"), "[preview] still labelled read-only and synthetic")
        check(not perr, f"[preview] no JavaScript errors {perr[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL FIX 4 CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
