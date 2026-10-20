"""Fix 5: static preview is honestly read-only; the full application is NOT. Chromium. Run: python3 tests/test_preview_readonly.py
Preview: every write control (buttons, forms, file inputs) is announced as unavailable (aria-disabled / disabled + exact message), a click shows the message and sends NO write request,
no success message ever appears, viewing/navigation/compare/tour/print controls still work, and the embedded snapshot is unchanged. Full app: the same controls are live, a real write succeeds
only after the backend confirms it, and a refused write shows an error and no success message."""
import os, sys, re, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
MSG = "Unavailable in the static preview. Open the full application to perform this action."
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
STRIP = lambda x: re.sub(r"Unavailable in the static preview.*", "", x)
SUCCESS = re.compile(r"\b(saved|stored|imported|approved|updated to|uploaded|recorded|extracted|reset to|rebuilt|generated)\b", re.I)
LOCKED = "[data-act=twinupd],[data-act=appr],[data-act=rej],[data-act=apprAll],[data-act=tsimport],[data-act=outc],[data-act=reset],[data-act=sampleRep],[data-act=sampleScan],[data-act=sampleTs],form[data-form],input[data-change=upload],input[data-change=tsprev]"
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}
TABS = ["overview", "since", "twin", "predict", "quality", "timeline", "reports", "tests", "cycle", "symptoms", "physio", "export"]
async def go(pg, pid, tab):
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/{tab}"); await pg.wait_for_selector("#tab .card, #tab .empty, #tab .ph, #tab table", timeout=15000); await pg.wait_for_timeout(450)
async def counts(pg): return await pg.evaluate("()=>Promise.all(['SYN-000','SYN-001','SYN-002'].map(p=>fetch('/api/patients/'+p+'/summary',{credentials:'same-origin'}).then(r=>r.json()).then(s=>JSON.stringify(s.counts))))")
async def toasts(pg): return await pg.evaluate("()=>[...document.querySelectorAll('#toast .t')].map(e=>e.textContent)")
async def preview(b):
    pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; writes = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("request", lambda r: writes.append(r.url) if r.method == "POST" and not re.search(r"/api/(demo-login|logout)$", r.url) else None)
    await pg.goto(PV); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
    base = await counts(pg); total = 0; bad = []
    for pid in NAMES:
        for t in TABS:
            await go(pg, pid, t)
            info = await pg.evaluate("""s=>[...document.querySelectorAll(s)].map(e=>({tag:e.tagName,act:e.dataset.act||e.dataset.form||e.dataset.change,aria:e.getAttribute('aria-disabled'),dis:e.disabled,title:e.title||'',desc:e.getAttribute('aria-describedby'),note:!!document.getElementById('pvnote'),inner:e.tagName==='FORM'?[...e.querySelectorAll('input,select,textarea,button')].every(c=>c.disabled)&&/Unavailable in the static preview/.test(e.innerText):true}))""", LOCKED)
            for x in info:
                total += 1; ok = x["aria"] == "true" and "Unavailable in the static preview" in (x["title"] if x["tag"] not in ("FORM",) else MSG) and x["note"] and x["inner"] and (x["tag"] in ("FORM", "INPUT") or x["desc"] == "pvnote") and (x["dis"] if x["tag"] == "INPUT" else True)
                if not ok: bad.append((pid, t, x))
    check(total > 20 and not bad, f"[preview] all {total} write controls across 3 patients x {len(TABS)} tabs are announced as unavailable (aria-disabled, exact message, page note) {bad[:2]}")
    # a click shows the message, sends nothing, and never shows a success message
    await go(pg, "SYN-000", "overview"); b1 = pg.locator("[data-act=twinupd]").first
    check(await b1.get_attribute("aria-disabled") == "true" and await b1.evaluate("e=>!e.disabled&&e.tabIndex>=0"), "[preview] 'Update Digital Twin' is focusable (aria-disabled, not removed from the tab order) so keyboard/screen-reader users hear why it is unavailable")
    await b1.focus(); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(300); tt = await toasts(pg)
    check(any(MSG in x for x in tt) and not any(SUCCESS.search(STRIP(x)) for x in tt), f"[preview] keyboard activation shows only the unavailable message {tt}")
    for pid, tab, sel in (("SYN-000", "reports", "[data-act=sampleScan]"), ("SYN-000", "physio", "[data-act=sampleTs]"), ("SYN-001", "twin", "[data-act=twinupd] >> nth=1")):
        await go(pg, pid, tab); el = pg.locator(sel).first
        if await el.count() == 0: check(False, f"[preview] {pid}/{tab}: control {sel} present"); continue
        await pg.evaluate("document.querySelector('#toast').innerHTML=''"); await el.dispatch_event("click"); await pg.wait_for_timeout(250); tt = await toasts(pg)
        check(any("Unavailable in the static preview" in x for x in tt) and not any(SUCCESS.search(STRIP(x)) for x in tt), f"[preview] {pid}/{tab}: click on {sel.split('>>')[0].strip()} -> unavailable message only {tt[:1]}")
    await go(pg, "SYN-000", "tests"); check(await pg.locator("form[data-form=addM] input").first.is_disabled() and "Unavailable in the static preview" in await pg.inner_text("form[data-form=addM]"), "[preview] 'Add a clinical measurement' form is disabled with the explanation inside it")
    await go(pg, "SYN-000", "reports"); check(await pg.locator("#ufile").is_disabled(), "[preview] report file chooser is disabled")
    await go(pg, "SYN-000", "predict"); check(await pg.locator("form[data-form=pred] button").first.is_disabled() and "Unavailable in the static preview" in await pg.inner_text("form[data-form=pred]"), "[preview] 'Run prediction' form is disabled with the explanation inside it (no new prediction can be created)")
    await go(pg, "SYN-002", "overview"); await go(pg, "SYN-002", "twin"); tx = await pg.inner_text("#tab"); check("No records yet for Kavya Reddy" in tx and not any(SUCCESS.search(STRIP(x)) for x in await toasts(pg)), "[preview] Kavya: empty record shown, nothing fabricated, no success message")
    # harmless viewing controls still work
    await go(pg, "SYN-000", "twin"); await pg.select_option("#ca", "3"); await pg.select_option("#cb", "1"); await pg.click("[data-act=cmp]"); await pg.wait_for_timeout(300); check("Version 1 (earlier) compared with version 3 (later)" in await pg.inner_text("#cmp"), "[preview] Twin version comparison still works")
    await pg.click("[data-act=replay][data-v='1']"); await pg.wait_for_timeout(200); check("State as it was in version 1" in await pg.inner_text("#replay"), "[preview] version replay still works")
    await pg.select_option(".psel select", "SYN-001"); await pg.wait_for_timeout(900); check("/SYN-001/" in await pg.evaluate("location.hash") and "Meera Sharma" in (await pg.inner_text(".pbar")).split("Kavya Reddy (SYN-002)")[-1], "[preview] patient switching still works")
    await go(pg, "SYN-000", "export"); pb = pg.locator("[data-act=briefPrint]"); check(await pb.get_attribute("aria-disabled") is None and await pb.is_enabled() and await pg.locator("[data-act=briefRefresh]").get_attribute("aria-disabled") is None, "[preview] Doctor brief 'Print / Save as PDF' and 'Regenerate' are not disabled")
    await go(pg, "SYN-000", "overview"); await pg.evaluate("ACT.tourStart()"); await pg.wait_for_selector("#tour", timeout=10000); await pg.click("[data-act=tourNext]"); await pg.wait_for_timeout(500); check(await pg.locator("#tour").count() == 1, "[preview] guided tour still starts and advances"); await pg.evaluate("ACT.tourExit()")
    # nothing was written, locally or remotely
    check(not writes, f"[preview] no write request was sent by any attempted action {writes[:2]}"); check(await counts(pg) == base, "[preview] embedded snapshot unchanged after every attempted action")
    check("STATIC READ-ONLY PREVIEW" in await pg.inner_text(".banner") and "Synthetic data" in await pg.inner_text(".banner"), "[preview] banner still states read-only + synthetic")
    check(not errs, f"[preview] no JavaScript errors {errs[:2]}")
async def live(b):
    pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
    locked = 0
    for pid in NAMES:
        for t in TABS:
            await go(pg, pid, t); locked += await pg.evaluate("s=>document.querySelectorAll(s).length", "[data-pvlock],#pvnote,.pvlock,.pvnote")
    check(locked == 0, f"[full app] no control is locked and no preview note appears on any of {3 * len(TABS)} pages")
    await go(pg, "SYN-000", "tests"); n0 = (await pg.evaluate("()=>fetch('/api/patients/SYN-000/summary').then(r=>r.json())"))["counts"]["verified_measurements"]
    f = pg.locator("form[data-form=addM]"); check(await f.locator("input[name=test]").is_enabled(), "[full app] 'Add a clinical measurement' form is enabled")
    await f.locator("input[name=test]").fill("Ferritin"); await f.locator("input[name=value]").fill("55"); await f.locator("input[name=unit]").fill("ng/mL"); await f.locator("input[name=date]").fill("2026-03-01")
    await pg.evaluate("document.querySelector('#toast').innerHTML=''"); await f.locator("button[type=submit], button:not([type])").first.click(); await pg.wait_for_timeout(1200); tt = await toasts(pg)
    n1 = (await pg.evaluate("()=>fetch('/api/patients/SYN-000/summary').then(r=>r.json())"))["counts"]["verified_measurements"]
    check(n1 == n0 + 1 and any("Measurement saved as a verified record" in x for x in tt), f"[full app] a real write succeeds: success message shown only after the backend confirmed it; verified count {n0} -> {n1}")
    await go(pg, "SYN-000", "tests"); tbl = await pg.inner_text("#tab"); check("Ferritin" in tbl, "[full app] the new record appears after the view refreshed from stored data")
    f = pg.locator("form[data-form=addM]"); await f.locator("input[name=test]").fill("Ferritin"); await f.locator("input[name=value]").fill("55"); await f.locator("input[name=unit]").fill("ng/mL"); await f.locator("input[name=date]").fill("2026-03-01")
    await pg.evaluate("document.querySelector('#toast').innerHTML=''"); await f.locator("button[type=submit], button:not([type])").first.click(); await pg.wait_for_timeout(1000); tt = await toasts(pg)
    n2 = (await pg.evaluate("()=>fetch('/api/patients/SYN-000/summary').then(r=>r.json())"))["counts"]["verified_measurements"]
    check(n2 == n1 and tt and not any(SUCCESS.search(x) for x in tt), f"[full app] a refused duplicate shows an error and NO success message; count unchanged ({n2}) {tt[:1]}")
    await go(pg, "SYN-000", "overview"); check(await pg.locator("[data-act=twinupd]").first.get_attribute("aria-disabled") is None, "[full app] 'Update Digital Twin' remains available")
    await go(pg, "SYN-000", "reports"); check(await pg.locator("#ufile").is_enabled() and "not assumed to be synthetic" in await pg.inner_text("#tab"), "[full app] upload stays available and the page explains that uploads are not assumed synthetic")
    check(not errs, f"[full app] no JavaScript errors {errs[:2]}")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
        await preview(b); await live(b); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL PREVIEW/LIVE INTERACTION CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
