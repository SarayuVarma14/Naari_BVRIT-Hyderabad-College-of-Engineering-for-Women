"""Guided-tour consistency test (needs playwright + chromium). Run: python3 tests/test_tour_browser.py
Checks every tour statement against API ground truth AND the rendered page, then follows the tour's own instructions."""
import os, sys, re, json, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try: urllib.request.urlopen(B + "api/health"); break
    except Exception: time.sleep(.5)
FAILS = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: FAILS.append(msg)
async def card(pg):
    await pg.wait_for_selector("#tour p:not(:has-text('Loading'))", timeout=15000)
    return (await pg.inner_text("#tour h3")), (await pg.inner_text("#tour p")), (await pg.inner_text("#tour small"))
async def api(pg, path): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", path)
async def page_ready(pg, sel): await pg.wait_for_selector(sel, timeout=15000); await pg.wait_for_timeout(500)
async def goto_step(pg, n):  # from current position go to step n (1-based) using Back/Next
    for _ in range(12):
        t = await pg.inner_text("#tour small"); cur = int(re.search(r"Step (\d+)", t).group(1))
        if cur == n: break
        await pg.click("#tour [data-act=tourNext]" if cur < n else "#tour [data-act=tourBack]"); await pg.wait_for_timeout(300); await card(pg)
async def main(preview=False):
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        if preview: U = "file:///mnt/user-data/outputs/naari_preview.html"; await pg.goto(U)
        else: await pg.goto(B)
        await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        await pg.click("button:has-text('Explore a patient journey')")
        T = {}
        for i in range(1, 8):
            T[i] = await card(pg); await page_ready(pg, ".pbar"); T[i] += (await pg.inner_text("#tab"), pg.url.split("#")[1])
            if i < 7: await pg.click("#tour [data-act=tourNext]")
        sm = await api(pg, "/api/patients/SYN-000/summary"); k = sm["counts"]
        tag = "[preview] " if preview else ""
        # step titles/paths match the real navigation labels
        nav = {1: "Overview", 2: "Lab tests", 3: "Menstrual cycle", 4: "Data quality", 5: "Twin & versions", 6: "Prediction", 7: "Doctor brief"}
        check(all(T[i][0] == nav[i] for i in nav), tag + "step titles equal the real navigation labels")
        check(all(T[i][4] == f"/p/SYN-000/{t}" for i, t in zip(range(1, 8), ["overview", "tests", "cycle", "quality", "twin", "predict", "export"])), tag + "each step opens the matching real route")
        t1 = T[1][1]; check("Ananya Rao" in t1 and f"{k['verified_measurements']} verified measurements" in t1 and f"{k['cycles']} recorded period starts" in t1 and f"{k['symptom_entries']} patient-reported symptom entries" in t1, tag + "step 1 counts equal API counts")
        check("Ananya Rao" in await pg.inner_text(".pbar"), tag + "patient bar shows the same patient as the tour")
        M = [m for m in await api(pg, "/api/patients/SYN-000/measurements") if m["verified"]]; names = {m["test"].lower() for m in M}
        t2 = T[2][1]; check(f"{len(names)} different tests" in t2, tag + f"step 2 test count = {len(names)}")
        ref = len({m["test"].lower() for m in M if m["ref_low"] is not None}); check(f"on {ref} of them" in t2, tag + f"step 2 reference-range count = {ref}")
        hb = any("hba1c" in n for n in names); check(("HbA1c is not recorded" in t2) == (not hb), tag + "step 2 HbA1c claim matches data")
        cs = await api(pg, "/api/patients/SYN-000/cycles/summary"); t3 = T[3][1]
        check(f"{cs['n_starts']} period starts" in t3 and f"from {cs['min']} to {cs['max']} days" in t3 and f"median {cs['median']:g}" in t3, tag + "step 3 cycle numbers equal API")
        await pg.goto((("file:///mnt/user-data/outputs/naari_preview.html" if preview else B)) + "#/p/SYN-000/cycle"); await page_ready(pg, ".cal"); n_est = await pg.locator(".cal .est").count()
        check((n_est == 1) == ("dashed calendar cell" in t3), tag + "step 3 dashed-estimate claim matches the rendered calendar")
        q = await api(pg, "/api/patients/SYN-000/quality"); t4 = T[4][1]; r = q["prediction"]
        check(("all required inputs are present" in t4) == bool(r["available"]) and all(x in t4 for x in (r.get("reasons") or [])), tag + "step 4 prediction status equals API readiness")
        S = await api(pg, "/api/patients/SYN-000/twin"); t5 = T[5][1]
        check(f"version {S[0]['version']}" in t5 and f"{len(S)} version" in t5, tag + f"step 5 version info equals API ({len(S)} versions)")
        check(("Compare two versions" in T[5][3]) == (len(S) > 1) and (("Use <b>Compare" in t5) or "Use Compare two versions" in t5) == (len(S) > 1), tag + "step 5 compare claim matches rendered page")
        t6 = T[6][1]; check(("not sufficient" in t6) == (not r["available"]) or preview, tag + "step 6 availability matches readiness")
        if preview:
            check("read-only preview" in t6 and "Approve all pending" not in t6, "[preview] step 6 says read-only and gives no upload instructions")
            P = await api(pg, "/api/patients/SYN-000/predictions"); check(f"{len(P)} stored prediction" in t6, "[preview] step 6 stored-prediction count equals data")
            check("embedded synthetic snapshot data" in T[7][1], "[preview] step 7 discloses the brief is built from embedded snapshot data")
        else:
            check("Use sample synthetic lab report (PDF)" in t6 and "Approve all pending" in t6, "step 6 names the exact sample file and button")
            check("embedded" not in T[7][1] and f"{k['verified_measurements']} measurements" in T[7][1], "step 7 counts equal API and make no preview claim")
        check(len(errs) == 0, tag + f"no JavaScript errors ({errs[:2]})")
        if preview: await b.close(); return
        # ---- follow the tour's own instructions literally, then confirm the tour text updates truthfully ----
        await pg.goto(B + "#/p/SYN-000/predict"); await page_ready(pg, ".pbar"); t, txt, sm_ = await card(pg)
        check("Tour paused" in sm_ and "left step" in t, "navigating away pauses the tour with a notice (no stale text)")
        await pg.click("#tour [data-act=tourExit]"); await pg.goto(B + "#/p/SYN-000/reports"); await page_ready(pg, "text=Use sample synthetic lab report (PDF)")
        await pg.click("text=Use sample synthetic lab report (PDF)"); await pg.wait_for_selector("#ft tr[data-f]", timeout=30000)
        await pg.click("text=Approve all pending"); await pg.wait_for_timeout(3500)
        await pg.goto(B + "#/workspace"); await pg.click("button:has-text('Explore a patient journey')"); 
        await card(pg); await goto_step(pg, 6); t6b = (await card(pg))[1]; await page_ready(pg, ".pbar")
        check("all present" in t6b and "not sufficient" not in t6b, "after following step 6, the tour says prediction is ready")
        check(await pg.locator("text=Inputs sufficient to run").count() == 1, "…and the Prediction page itself agrees")
        await goto_step(pg, 5); t5b = (await card(pg))[1]; await page_ready(pg, ".pbar")
        check("version 2" in t5b and "2 versions" in t5b and await pg.locator("#ca").count() == 1, "step 5 now reports version 2 and Compare is really on the page")
        await goto_step(pg, 4); t4b = (await card(pg))[1]; check("all required inputs are present" in t4b and "no verified HbA1c" not in t4b, "step 4 no longer claims HbA1c is missing")
        await goto_step(pg, 2); t2b = (await card(pg))[1]; await page_ready(pg, ".pbar"); check("HbA1c is not recorded" not in t2b and "HbA1c" in t2b, "step 2 now lists HbA1c")
        check(await pg.locator("#tab >> text=HbA1c").count() >= 1, "…and the Lab tests page shows an HbA1c chart")
        # ---- patient switch mid-tour ----
        await pg.select_option("select[data-change=switchP]", "SYN-002"); await page_ready(pg, ".pbar .big:has-text('Kavya Reddy')")
        t, txt, sm_ = await card(pg); check("Tour paused" in sm_ and "Ananya" not in await pg.inner_text("#tab"), "switching patient pauses the tour; no Ananya data on Kavya's page")
        await pg.click("#tour [data-act=tourReturn]"); await page_ready(pg, ".pbar"); check("Ananya Rao" in await pg.inner_text(".pbar") and "Paused" not in await pg.inner_text("#tour small"), "Return to step restores Ananya and the step text")
        # ---- API failure inside the tour ----
        await pg.route("**/api/patients/SYN-000/measurements", lambda r: r.fulfill(status=500, body='{"error":"boom"}', content_type="application/json"))
        await pg.click("#tour [data-act=tourNext]"); await card(pg); await pg.click("#tour [data-act=tourBack]"); t, txt, _ = await card(pg); check("Could not load" in txt, "a failed data request shows a clear error in the tour (no invented text)")
        await pg.unroute("**/api/patients/SYN-000/measurements")
        await pg.click("#tour [data-act=tourExit]"); check(await pg.locator("#tour").count() == 0, "Exit tour removes the card")
        check(len(errs) == 0, f"no JavaScript errors in the full run ({errs[:2]})"); await b.close()
try:
    asyncio.run(main(preview=False))
    subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
    asyncio.run(main(preview=True))
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL TOUR CHECKS PASSED", *FAILS, sep="\n")
sys.exit(1 if FAILS else 0)
