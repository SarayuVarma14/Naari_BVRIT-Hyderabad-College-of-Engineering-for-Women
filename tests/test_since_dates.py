"""Since-last-visit custom date comparisons (live app + static preview, Chromium). Run: python3 tests/test_since_dates.py
Checks: a custom range changes the comparison (not just the displayed dates); rendered rows equal an independent calculation from the raw records; invalid / missing / reversed
ranges are refused with an inline message and the previous results are cleared; empty periods say so without inventing values; the preview computes only from embedded data, sends no write
request and reaches the same answers as the full application; sources and dates stay visible; non-Twin sections are labelled as such."""
import os, sys, re, json, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
flat = lambda t: re.sub(r"\s+", " ", t)
RANGES = [("2025-05-01", "2025-08-01"), ("2025-11-01", "2026-01-31"), ("2026-02-20", "2026-03-14"), ("2025-12-14", "2026-03-14"), ("2024-01-01", "2024-02-01"), ("2026-03-09", "2026-03-11")]
KEYS = ["from", "to", "prev_from", "reference_date", "new_measurements", "changed_measurements", "cycles", "symptoms", "twin", "awaiting_review"]
async def api(pg, p):
    return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(async r=>({status:r.status,body:await r.json()}))", p)
async def go(pg, pid, tab):
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/{tab}"); await pg.wait_for_selector("#tab .card, #tab .empty", timeout=15000); await pg.wait_for_timeout(500)
async def compare(pg, frm, to):
    await pg.fill("#sf", frm) if frm else await pg.fill("#sf", ""); await pg.fill("#st", to) if to else await pg.fill("#st", ""); await pg.click("[data-act=sinceGo]"); await pg.wait_for_timeout(700)
    return flat(await pg.inner_text("#sr"))
def expected_cycles(cy, frm, to): return [c["start"] for c in sorted(cy, key=lambda x: x["start"]) if frm < c["start"] <= to]
async def ui_checks(pg, label, preview, writes):
    await go(pg, "SYN-000", "since"); await pg.wait_for_selector("#sf")
    cy = (await api(pg, "/api/patients/SYN-000/cycles"))["body"]; ms = [m for m in (await api(pg, "/api/patients/SYN-000/measurements"))["body"] if m["verified"]]
    shown = []
    for frm, to in RANGES:
        t = await compare(pg, frm, to); n_cy = len(re.findall(r"Period start", t)); exp = expected_cycles(cy, frm, to); n_ms = len(re.findall(r"source: ", t)); exp_ms = [m for m in ms if frm < m["date"] <= to]
        check(f"up to and including" in t and re.search(r"Showing changes after \d+ \w+ \d{4} up to and including \d+ \w+ \d{4}", t) is not None, f"[{label}] {frm}→{to}: header states the range actually applied")
        check(n_cy == len(exp), f"[{label}] {frm}→{to}: {n_cy} period starts shown = {len(exp)} in the raw records")
        check(n_ms == len(exp_ms) and all(m["test"] in t for m in exp_ms), f"[{label}] {frm}→{to}: {n_ms} new verified measurements shown = {len(exp_ms)} in the raw records, each with its source")
        shown.append((n_cy, n_ms, t))
    check(len({(a, b) for a, b, _ in shown}) >= 3, f"[{label}] different custom ranges give different comparisons (not just different dates): {[(a, b) for a, b, _ in shown]}")
    empt = shown[4][2]; check("No new verified results in this period." in empt and "No new periods recorded in this period." in empt and "No symptom entries in this period." in empt and "Period start" not in empt, f"[{label}] empty period (2024) says so in each section and shows no values")
    check("not Twin inputs" in shown[0][2] and "Only verified measurements and the glucose summary feed the Digital Twin" in shown[0][2], f"[{label}] page states which sections are NOT Digital Twin inputs")
    for frm, to, msg in (("2026-03-14", "2025-01-01", "The start date must be earlier"), ("2026-03-01", "2026-03-01", "The start date must be earlier"), ("", "2026-03-01", "Choose both"), ("2026-03-01", "", "Choose both")):
        t = await compare(pg, frm, to); check(msg in t and "Comparison not run" in t and "Period start" not in t and "Showing changes" not in t, f"[{label}] invalid range ({frm or '—'} → {to or '—'}): '{msg}…', stale results cleared")
    # Twin versions in the window + Twin section wording
    t = await compare(pg, "2026-03-01", "2026-03-14"); check("Twin version" in t and "values added or changed" in t, f"[{label}] 1–14 Mar 2026: Twin versions in the window are listed with their change counts")
    t = await compare(pg, "2024-01-01", "2024-02-01"); check("No twin version covers this period." in t or "No new versions." in t, f"[{label}] empty period: Twin section does not invent a version")
    # other patients
    await go(pg, "SYN-002", "since"); check("Nothing to compare yet" in flat(await pg.inner_text("#tab")), f"[{label}] Kavya: 'Nothing to compare yet' (no fabricated comparison)")
    await go(pg, "SYN-001", "since"); t = await compare(pg, "2025-01-01", "2026-03-14"); check("Comparison not run" not in t and "Showing changes" in t, f"[{label}] Meera: custom range runs on her limited record")
    if preview:
        await go(pg, "SYN-000", "since"); t = await compare(pg, "2025-11-01", "2026-03-14"); check("Wearable counts for custom dates are not available in the static preview" in t, f"[{label}] preview states that wearable counts are unavailable for custom dates (not invented)")
        check(not writes, f"[{label}] no write request and no unavailable-endpoint request was made {writes[:2]}")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        # same state as the preview: Ananya's demo workflow
        await pg.evaluate("""async()=>{const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());const d=await fetch('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',credentials:'same-origin',body:png}).then(r=>r.json());const H={'Content-Type':'application/json'};
          for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await fetch('/api/fields/'+f.id,{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
          const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await fetch('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',credentials:'same-origin',body:csv});
          await fetch('/api/patients/SYN-000/twin/update',{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})})}""")
        # server validation
        for q, st in (("from=2026-03-14&to=2025-01-01", 422), ("from=abc&to=2026-01-01", 422), ("from=2026-03-01&to=2026-03-01", 422)):
            r = await api(pg, f"/api/patients/SYN-000/since?{q}"); check(r["status"] == st, f"[server] since?{q} -> {r['status']} (expected {st})")
        live = {}
        for pid in ("SYN-000", "SYN-001"):
            for frm, to in RANGES: live[(pid, frm, to)] = (await api(pg, f"/api/patients/{pid}/since?from={frm}&to={to}"))["body"]
        check(all(r.get("from") == k[1] and r.get("to") == k[2] for k, r in live.items()), "[server] every response reports exactly the requested range")
        check(len({json.dumps([r["cycles"], r["new_measurements"]]) for k, r in live.items() if k[0] == "SYN-000"}) >= 3, "[server] different ranges return different data")
        await ui_checks(pg, "full app", False, [])
        check(not errs, f"[full app] no JavaScript errors {errs[:2]}")
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; bad = []; pv.on("pageerror", lambda e: perr.append(str(e)))
        pv.on("request", lambda r: bad.append(r.method + " " + r.url) if r.method == "POST" and not re.search(r"/api/(demo-login|logout)$", r.url) else None)
        pv.on("response", lambda r: bad.append(f"{r.status} {r.url}") if "/since" in r.url and r.status in (403, 404) else None)
        await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient")
        # preview answers == full-application answers for the same dates (physiology excluded: not embedded)
        diffs = []
        for (pid, frm, to), r in live.items():
            pr = await api(pv, f"/api/patients/{pid}/since?from={frm}&to={to}")
            for k in KEYS:
                if json.dumps(pr["body"].get(k), sort_keys=True) != json.dumps(r.get(k), sort_keys=True): diffs.append((pid, frm, to, k))
        check(not diffs, f"[preview] local calculation equals the full application for {len(live)} (patient, range) pairs, keys {KEYS} {diffs[:3]}")
        for q in ("from=2026-03-14&to=2025-01-01", "from=abc&to=2026-01-01"):
            r = await api(pv, f"/api/patients/SYN-000/since?{q}"); check(r["status"] == 422, f"[preview] since?{q} -> {r['status']} (422 like the server)")
        await ui_checks(pv, "static preview", True, bad)
        check(not perr, f"[static preview] no JavaScript errors {perr[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL SINCE-DATE CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
