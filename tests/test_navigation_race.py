"""Final-stabilization regression: an older, slower navigation response must never overwrite a newer navigation. Live app, Chromium.
Run: python3 tests/test_navigation_race.py
Sequence tested (as specified): 1) start navigation to patient A, 2) before it completes navigate to patient B, 3) let B's response complete,
4) only then complete A's older response, 5) the screen must still show B's identity and data. Repeated for several pages (overview, Digital Twin, Doctor brief),
for A's /api/me response being the slow one, and for the workspace list. Also checks normal navigation, browser Back, and patient switching still work."""
import os, sys, re, time, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
flat = lambda t: re.sub(r"\s+", " ", t)
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}
async def state(pg):
    return await pg.evaluate("""()=>({sel:(document.querySelector('.psel select')||{}).value||null,hash:location.hash,PID:typeof PID!=='undefined'?PID:null,sum:(typeof SUM!=='undefined'&&SUM)?SUM.patient:null,
      head:(document.querySelector('.pbar')?document.querySelector('.pbar').innerText.replace(/\\s+/g,' '):''),tab:(document.querySelector('#tab')||{innerText:''}).innerText.replace(/\\s+/g,' ').slice(0,4000)})""")
async def race(pg, gate, tab, slow_pattern, label, slow_pid="SYN-000", fast_pid="SYN-001"):
    """slow_pattern: URL substring whose response for slow_pid is held back until released."""
    release = asyncio.Event(); held = []
    async def handler(route):
        if slow_pid in route.request.url and slow_pattern in route.request.url:
            held.append(route.request.url); await release.wait()
        await route.continue_()
    await pg.route("**/api/**", handler)
    await pg.evaluate("h=>{location.hash='#/workspace'}", None); await pg.wait_for_selector("#pl a.card"); await pg.wait_for_timeout(200)
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{slow_pid}/{tab}"); await pg.wait_for_timeout(400)           # 1. navigate to A (its response is held)
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{fast_pid}/{tab}")                                            # 2. navigate to B before A completes
    await pg.wait_for_function(f"()=>document.querySelector('.pbar')&&document.querySelector('.pbar').innerText.includes('{NAMES[fast_pid]} (SYN')&&document.querySelector('#tab')&&document.querySelector('#tab').innerText.length>30&&!/Generating the brief|Loading/.test(document.querySelector('#tab').innerText)", timeout=15000)   # 3. B completes
    before = await state(pg); check(len(held) >= 1, f"[{label}] A's {slow_pattern} request was actually held back ({len(held)} held)")
    release.set(); await pg.wait_for_timeout(1500)                                                                # 4. A's older response completes afterwards
    after = await state(pg); await pg.unroute("**/api/**", handler)
    check(after["sel"] == fast_pid and after["PID"] == fast_pid and after["sum"] == fast_pid and f"{NAMES[fast_pid]}" in after["head"].split("Kavya Reddy (SYN-002)")[-1][:80] and f"{fast_pid} · age" in after["head"], f"[{label}] 5. screen still shows {NAMES[fast_pid]} ({fast_pid}) — selector={after['sel']} PID={after['PID']} SUM={after['sum']}")
    check(NAMES[slow_pid] not in after["head"].split("Kavya Reddy (SYN-002)")[-1][:200] and after["hash"].startswith(f"#/p/{fast_pid}/"), f"[{label}] patient A's identity does not appear in the header; URL still {after['hash']}")
    check(after["tab"] == before["tab"], f"[{label}] page content unchanged by the late response from A")
    return after
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        for tab in ("overview", "twin", "export"): await race(pg, None, tab, "/summary", f"{tab}: A's /summary slow")
        # slow patient-level data inside the view (not the summary)
        await race(pg, None, "twin", "/twin", "twin: A's /twin data slow")
        await race(pg, None, "overview", "/journey", "overview: A's /journey data slow")
        # slow /api/me for the A navigation (the first await inside route())
        rel = asyncio.Event(); calls = {"n": 0}
        async def me_handler(route):
            if route.request.url.endswith("/api/me"):
                calls["n"] += 1
                if calls["n"] == 1: await rel.wait()
            await route.continue_()
        await pg.evaluate("location.hash='#/workspace'"); await pg.wait_for_selector("#pl a.card"); await pg.wait_for_timeout(300)
        await pg.route("**/api/**", me_handler); calls["n"] = 0
        await pg.evaluate("location.hash='#/p/SYN-000/overview'"); await pg.wait_for_timeout(300); await pg.evaluate("location.hash='#/p/SYN-002/overview'")
        await pg.wait_for_function("()=>document.querySelector('.pbar')&&document.querySelector('.pbar').innerText.includes('Kavya Reddy (SYN')", timeout=15000); rel.set(); await pg.wait_for_timeout(1500); s = await state(pg); await pg.unroute("**/api/**", me_handler)
        check(s["sel"] == "SYN-002" and s["PID"] == "SYN-002" and s["sum"] == "SYN-002", f"[route-level] A's slow /api/me cannot reclaim the page: selector={s['sel']} PID={s['PID']} SUM={s['sum']}")
        # workspace list (slow) then patient navigation: late workspace render must not replace the patient page
        rel2 = asyncio.Event()
        async def ws_handler(route):
            if route.request.url.endswith("/api/patients"): await rel2.wait()
            await route.continue_()
        await pg.route("**/api/**", ws_handler); await pg.evaluate("location.hash='#/workspace'"); await pg.wait_for_timeout(300); await pg.evaluate("location.hash='#/p/SYN-001/overview'")
        await pg.wait_for_timeout(300); rel2.set(); await pg.wait_for_function("()=>document.querySelector('.pbar')", timeout=15000); await pg.wait_for_timeout(1200); s = await state(pg); await pg.unroute("**/api/**", ws_handler)
        check(s["sel"] == "SYN-001" and "Choose a patient" not in await pg.inner_text("#app"), "[workspace] a slow workspace list cannot replace the patient page the user moved on to")
        # normal behaviour preserved
        for pid in ("SYN-000", "SYN-001", "SYN-002"):
            await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/twin"); await pg.wait_for_selector("#tab .card"); await pg.wait_for_timeout(300); s = await state(pg)
            check(s["sel"] == pid and s["sum"] == pid and NAMES[pid] in s["head"].split("Kavya Reddy (SYN-002)")[-1][:80], f"[normal] navigating to {pid} shows {NAMES[pid]}")
        await pg.go_back(); await pg.wait_for_timeout(800); s = await state(pg); check(s["sel"] == "SYN-001" and s["sum"] == "SYN-001", f"[history] browser Back returns to the previous patient (selector {s['sel']})")
        await pg.select_option(".psel select", "SYN-002"); await pg.wait_for_timeout(900); s = await state(pg); check(s["sel"] == "SYN-002" and "/SYN-002/" in s["hash"], "[switcher] patient dropdown still switches patient")
        check(not errs, f"[live app] no JavaScript errors {errs[:2]}"); await b.close()
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL NAVIGATION-RACE CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
