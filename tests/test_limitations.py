"""Fix 5B: limitations and clinical meaning are stated clearly and consistently (live app + static preview, 3 patients). Chromium. Run: python3 tests/test_limitations.py"""
import os, sys, re, json, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
flat = lambda t: re.sub(r"\s+", " ", t)
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}
TABS = ["overview", "since", "twin", "predict", "quality", "timeline", "reports", "tests", "cycle", "symptoms", "physio", "export"]
async def go(pg, pid, tab):
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/{tab}"); await pg.wait_for_selector("#tab .card, #tab .empty, #tab .ph, #tab table", timeout=15000); await pg.wait_for_timeout(400)
async def checks(pg, label, preview):
    foot = flat(await pg.inner_text("footer")); ban = flat(await pg.inner_text(".banner"))
    check("[Team name]" not in foot and "[College name]" not in foot and "[" not in foot.replace("[", "", 0).split("Clinical reference")[0], f"[{label}] footer has no [Team name]/[College name] placeholders: '{foot[-90:]}'")
    check("not a diagnostic or treatment system" in foot.lower() or "not a diagnostic or treatment system" in foot, f"[{label}] footer keeps the research-prototype / not-diagnostic statement")
    check("not labelled synthetic" in foot and "seeded patients and records are synthetic" in foot, f"[{label}] footer distinguishes seeded synthetic records from uploaded/entered values")
    check("Not clinically validated" in ban and ("read-only" in ban.lower()) == preview and "ynthetic" in ban, f"[{label}] banner: not clinically validated, synthetic wording{', read-only' if preview else ''}")
    for pid, nm in NAMES.items():
        await go(pg, pid, "predict"); t = flat(await pg.inner_text("#tab"))
        check("above 180 mg/dL" in t and "next 2 hours" in t and "at or below 180" in t, f"[{label}] {pid}: prediction page states the exact task (glucose >180 mg/dL within 2 h, only when currently <=180)")
        check(all(x in t.lower() for x in ("trained on synthetic data only", "not a pcos diagnosis", "not a forecast of pcos progression", "not a validated risk")), f"[{label}] {pid}: prediction page says synthetic-only training, not a diagnosis, not a forecast of progression, not a validated risk")
        bad = [m.group(0) for m in re.finditer(r".{0,40}validated.{0,20}", t) if not re.search(r"\b(not|no|never|has not been|un)\b", m.group(0), re.I)]
        check(not bad, f"[{label}] {pid}: every mention of 'validated' on the prediction page is a negation {bad[:1]}")
        await go(pg, pid, "reports"); r = flat(await pg.inner_text("#tab"))
        check("pending" in r.lower() or "No reports uploaded" in r or "Upload" in r, f"[{label}] {pid}: reports page present")
        await go(pg, pid, "twin"); tw = flat(await pg.inner_text("#tab"))
        check("research data summary, not a diagnosis" in tw and ("none built yet" in tw) == (pid != "SYN-000"), f"[{label}] {pid}: Twin page states scope; 'none built yet' appears exactly when no Twin is saved")
        B_ = await pg.evaluate("p=>fetch('/api/patients/'+p+'/brief',{credentials:'same-origin'}).then(r=>r.json())", pid); n = B_["notices"]
        check(nm in n["synthetic"] and "fictional patient" in n["synthetic"] and "uploaded or entered values are shown as provided" in n["synthetic"] and all(o not in " ".join(str(v) for v in n.values()) for k, o in NAMES.items() if k != pid), f"[{label}] {pid}: Doctor Brief notice names only {nm}, says seeded records are synthetic and uploaded/entered values are not assumed synthetic")
        allb = flat(json.dumps(B_)); check("not a diagnostic or treatment" in allb.lower() and "not been clinically validated" in allb and "does not diagnose PCOS" in allb, f"[{label}] {pid}: brief says not diagnostic/treatment, model not clinically validated, does not diagnose PCOS")
    await go(pg, "SYN-000", "reports"); r = flat(await pg.inner_text("#tab"))
    check("not assumed to be synthetic" in r and "stay pending" in r and "only approved values become verified measurements" in r, f"[{label}] upload area explains: seeded = fictional, uploads not assumed synthetic, pending until approved, only approved = verified")
    await go(pg, "SYN-001", "twin"); m = flat(await pg.inner_text("#tab")); check("No readings recorded" in m and "Unavailable — no glucose readings recorded" in m and "None recorded" in m, f"[{label}] Meera: missing data is shown as missing/unavailable, never as a normal or zero result")
    await go(pg, "SYN-002", "twin"); k = flat(await pg.inner_text("#tab")); check("No records yet for Kavya Reddy" in k and "none built yet" in k, f"[{label}] Kavya: no records and no saved Twin stated; nothing fabricated")
    await go(pg, "SYN-000", "predict"); p = flat(await pg.inner_text("#tab")); check(re.search(r"observed|outcome", p, re.I) is not None, f"[{label}] Ananya: prediction page distinguishes model scores from observed outcomes")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        for label, url, prev in (("full app", B, False), ("static preview", PV, True)):
            pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); await pg.goto(url); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
            await checks(pg, label, prev); check(not errs, f"[{label}] no JavaScript errors {errs[:2]}")
        await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL LIMITATION CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
