"""Doctor Brief tests (full app, then static preview). Run: python3 tests/test_brief_browser.py
Generates the brief for each patient, compares it with API ground truth, tests isolation/races/failures, and inspects real print output (page.pdf)."""
import os, sys, re, json, time, io, subprocess, tempfile, asyncio, urllib.request, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
import pdfplumber
from pypdf import PdfReader
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
def dfmt(d): return dt.datetime.strptime(d[:10], "%Y-%m-%d").strftime("%-d %b %Y")
async def api(pg, path): return await pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(r=>r.json())", path)
async def ws(pg, base):                                    # let the workspace finish rendering before navigating on (router has no latest-wins guard)
    await pg.goto(base + "#/workspace"); await pg.wait_for_selector("text=Choose a patient")
async def open_brief(pg, base, pid):
    await pg.goto(base + f"#/p/{pid}/export"); await pg.wait_for_selector(f"#tab .paper:has-text('{NAMES[pid]}')", timeout=20000); await pg.wait_for_selector("[data-act=briefPrint]", timeout=20000)
async def paper(pg): return await pg.inner_text("#tab .paper")
async def proot(pg): return await pg.evaluate("(document.querySelector('#printroot')||{}).textContent||''")
def others(pid): return [n for i, n in NAMES.items() if i != pid] + [i for i in NAMES if i != pid]
async def verify(pg, pid, tag, pdfonly=False):
    A = lambda p: api(pg, f"/api/patients/{pid}/{p}")
    S, M, C, SY, CS, TW, PR, Q = [await A(x) for x in ("summary", "measurements", "cycles", "symptoms", "cycles/summary", "twin", "predictions", "quality")]
    name = S["profile"]["name"]; txt = await paper(pg); pr = await proot(pg); rows = await pg.eval_on_selector_all("#tab .paper tbody tr", "e=>e.map(x=>x.innerText)")
    check(name in txt and pid in txt and (str(S["profile"]["age"]) in txt), f"{tag}: identity (name, ID, age) present")
    check(all(o not in txt and o not in pr for o in others(pid)), f"{tag}: no other patient's name or ID anywhere (screen + print copy)")
    check(not re.search(r"measurement #|field #|document_id", txt), f"{tag}: no internal record IDs in the report body")
    check("Synthetic demonstration data" in txt and "not a diagnostic" in txt and "does not diagnose PCOS" in txt, f"{tag}: synthetic + not-diagnostic + glucose-only disclaimers")
    for h in S["history"]: check(h["text"] in txt and h["provenance"] in txt, f"{tag}: history '{h['text'][:28]}…' with provenance")
    if not S["history"]: check("No history entries are recorded" in txt, f"{tag}: empty history stated honestly")
    for m in M:
        ok = any(m["test"] in r and f"{m['value']:g}" in r and m["unit"] in r and dfmt(m["date"]) in r for r in rows); check(ok, f"{tag}: measurement row {m['test']} {m['value']:g} {m['unit']} {dfmt(m['date'])}")
        if m["ref_low"] is not None: check(any(m["test"] in r and f"{m['ref_low']:g}" in r and f"{m['ref_high']:g}" in r for r in rows), f"{tag}: {m['test']} reference range shown")
    if not M: check("No laboratory tests or vital-sign measurements are recorded" in txt and await pg.locator("#tab .paper h3:has-text('Laboratory tests')").count() == 0, f"{tag}: no measurement table is fabricated")
    for c in C: check(dfmt(c["start"]) in txt, f"{tag}: period start {dfmt(c['start'])}")
    if CS["lengths"]: check(f"median {CS['median']:g}" in txt and f"{CS['min']}–{CS['max']} days" in txt and "between one recorded period start and the next" in txt, f"{tag}: cycle statistics + method sentence match API")
    if not C: check("No periods are recorded" in txt, f"{tag}: empty cycles stated")
    if SY: check(f"{len(SY)} entries" in txt and "0 none" in txt, f"{tag}: symptom count {len(SY)} and scale")
    else: check("No symptom entries are recorded" in txt, f"{tag}: empty symptoms stated")
    for c in Q["coverage"]: check(f"{c['n']:,}" in txt and c["unit"] in txt and f"{c['coverage_pct']:g}%" in txt, f"{tag}: wearable {c['metric']} readings/unit/coverage")
    if not Q["coverage"]: check("No wearable data has been imported" in txt, f"{tag}: no wearable data stated (none implied)")
    if TW: check(f"Latest version: {TW[0]['version']}" in txt and "are not twin features" in txt, f"{tag}: twin version {TW[0]['version']} + scope statement")
    else: check("No Digital Twin version has been built" in txt, f"{tag}: no twin stated")
    if PR: check(all(f"{p['prob']:.3f}" in txt for p in PR), f"{tag}: stored predictions shown")
    else: check("No prediction has been run" in txt, f"{tag}: no prediction invented")
    check(await pg.locator("#tab .paper h2").count() == 9, f"{tag}: all 9 sections present")
async def print_pdf(pg, pid, path):
    await pg.evaluate("()=>{window.print=()=>{window.__printed=document.title}}"); await pg.click("[data-act=briefPrint]"); title = await pg.evaluate("window.__printed")
    data = await pg.pdf(format="A4", print_background=True); open(path, "wb").write(data); return title, data
def inspect_pdf(data, pid, tag):
    pdf = pdfplumber.open(io.BytesIO(data)); T = [(p.extract_text() or "") for p in pdf.pages]; full = "\n".join(T)
    check(NAMES[pid] in full and pid in full and "NAARI — Doctor Brief" in full, f"{tag}: printed PDF has identity + title ({len(T)} page(s))")
    check(all(o not in full for o in others(pid)), f"{tag}: printed PDF has no other patient's name/ID")
    check(all("research prototype and not a diagnostic" in t for t in T), f"{tag}: footer on every page")
    chrome = ["Update Digital Twin", "Sign out", "Viewing patient", "Print / Save as PDF", "Regenerate", "Seeded demo data is synthetic ·"]
    check(not any(c in full for c in chrome), f"{tag}: no app navigation/buttons in print output")
    check(all(f"{i} · " in full for i in range(1, 10)), f"{tag}: all nine numbered sections printed")
    check(all(max([w["x1"] for w in p.extract_words()] or [0]) <= p.width + .5 and min([w["x0"] for w in p.extract_words()] or [0]) >= -.5 for p in pdf.pages), f"{tag}: no text clipped beyond page edges")
    orphan = False
    for t in T:
        L = [l for l in t.split("\n")]; k = next((i for i, l in enumerate(L) if l.startswith("NAARI is a research prototype")), len(L)); body = [l for l in L[:k] if l.strip()]
        if body and re.match(r"^\d · ", body[-1]): orphan = True
    check(not orphan, f"{tag}: no section heading stranded at the bottom of a page")
    title = PdfReader(io.BytesIO(data)).metadata.title or ""; check(NAMES[pid] in title and pid in title, f"{tag}: PDF title metadata is patient-specific ('{title}')")

def _txt(data): return "\n".join((p.extract_text() or "") for p in pdfplumber.open(io.BytesIO(data)).pages)
def _n(x): return re.sub(r"\s+", "", x)
HEADERS = ["Date", "Type", "Entry", "Source of information", "Period start", "Period end", "Bleeding days (derived)", "Days to next recorded start (derived)", "Note", "Symptom", "Entries", "Test", "Value", "Unit", "Collected", "Laboratory reference range", "Lab-reported flag",
           "Status", "Source", "Stream", "Readings", "Period covered", "Coverage*", "Gaps", "Out-of-range (excluded)", "As of", "Target", "Model score", "Model version", "Observed outcome", "Patient", "Patient ID", "Age", "Report generated", "Records up to"]
def layout_only(tok): return tok == "•" or any(tok in h.lower() for h in HEADERS)          # bullet glyph, or a (possibly wrapped) table-header label
def facts(B):
    para = [B["overview"]["text"], B["overview"]["diagnosis"], B["notices"]["synthetic"], B["notices"]["footer"], B["cycles"]["text"] if B["cycles"] else None, B["symptoms"].get("text") if B["symptoms"] else None, B["measurements"]["note"] if (B["measurements"]["labs"] or B["measurements"]["vitals"]) else None, B["physiology"]["note"] if B["physiology"]["streams"] else None, B["physiology"]["derived"],
            B["twin"].get("text"), B["twin"].get("heading"), B["predictions"]["text"], B["predictions"]["none"]] + list(B["twin"].get("changes", [])) + list(B["twin"].get("history", [])) + [x for g in B["review_items"] if g["items"] for x in [g["title"]] + g["items"]]
    cells = [B["name"], B["patient"], str(B["age"]) if B["age"] is not None else "not recorded", B["generated"], B["reference_date"]]
    for h in B["history"]: cells += [h["date"], h["type"], h["text"], h["provenance"]]
    for grp in ("labs", "vitals"):
        for r in B["measurements"][grp]: cells += [r["test"], r["value"], r["unit"], r["collected"], r["reference"], r["flag"], r["status"], r["source"]]
    for r in B["cycles"]["rows"]: cells += [r["start"], r["end"], r["bleeding"], r["to_next"]]
    if B["symptoms"].get("rows"): 
        for r in B["symptoms"]["rows"]: cells += [r["symptom"], r["entries"]] + [c for c in r["cells"] if c]
        cells += B["symptoms"]["months"]
    for r in B["physiology"]["streams"]: cells += [r["stream"], r["unit"], r["readings"], r["period"], r["coverage"], r["gaps"], r["flagged"]]
    for r in B["predictions"]["rows"]: cells += [r["as_of"], r["target"], r["score"], r["model"], r["outcome"]]
    return [x for x in para if x], [x for x in cells if x]
async def parity(pg, base, pid, browser_pdf, tag):
    B = await api(pg, f"/api/patients/{pid}/brief"); resp = await pg.context.request.get(base + f"api/patients/{pid}/report.pdf"); body = await resp.body(); disp = resp.headers.get("content-disposition", "")
    check(resp.status == 200 and body[:4] == b"%PDF" and pid in disp and B["name"].split()[0] in disp and B["generated_iso"] in disp, f"{tag}: server PDF downloads with a patient-specific filename ({disp.split('filename=')[-1]})")
    open(f"/tmp/server_{pid}.pdf", "wb").write(body); inspect_pdf(body, pid, f"{tag} [server PDF]")
    P, C = facts(B); st, bt = _n(_txt(body)), _n(_txt(browser_pdf)); miss = {"server": [], "browser": []}
    for name, T in (("server", st), ("browser", bt)):
        for f in P:
            if _n(f) not in T: miss[name].append(f[:60])
        for f in C:
            if not all(tok in T for tok in f.split()): miss[name].append(f[:60])
    check(not miss["server"], f"{tag}: every fact in the shared structure appears in the SERVER PDF ({len(P)} statements + {len(C)} cells){miss['server'][:3]}")
    check(not miss["browser"], f"{tag}: every fact in the shared structure appears in the BROWSER-PRINTED PDF{miss['browser'][:3]}")
    norm = lambda x: x.lower().replace("(cid:127)", "•")                                   # layout-only differences: header capitalisation, bullet glyph extraction
    stoks, btoks = set(re.findall(r"\S+", norm(_txt(body)))), set(re.findall(r"\S+", norm(_txt(browser_pdf)))); stn, btn = _n(norm(_txt(body))), _n(norm(_txt(browser_pdf)))
    only_s = sorted(t for t in stoks - btoks if _n(t) not in btn and t != "page" and not layout_only(t)); only_b = sorted(t for t in btoks - stoks if _n(t) not in stn and not layout_only(t))
    print(f"INFO {tag}: tokens only in server PDF (not explained by wrapping/page numbers): {only_s[:6]} | only in browser PDF: {only_b[:6]}")
    check(not only_s and not only_b, f"{tag}: no content exists in one PDF that is absent from the other (symmetric token comparison)")
    sb = _n(_txt(body)); check(sb.count(_n(B["overview"]["text"])) == 1 and bt.count(_n(B["overview"]["text"])) == 1, f"{tag}: overview statement identical and present exactly once in both outputs")
async def run(base, preview):
    tag = "[preview] " if preview else "[full app] "
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(base); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        for pid in NAMES:
            await open_brief(pg, base, pid); await verify(pg, pid, f"{tag}{NAMES[pid]}")
            title, data = await print_pdf(pg, pid, f"/tmp/brief_{'pv_' if preview else ''}{pid}.pdf")
            check(NAMES[pid] in title and pid in title and dt.date.today().isoformat() in title, f"{tag}{NAMES[pid]}: print title is patient-specific ('{title}')"); inspect_pdf(data, pid, f"{tag}{NAMES[pid]}")
            if not preview: await parity(pg, base, pid, data, f"{tag}{NAMES[pid]}")
        # switching patients through the real selector
        await open_brief(pg, base, "SYN-000"); await pg.select_option("select[data-change=switchP]", "SYN-001"); await pg.wait_for_selector("#tab .paper:has-text('Meera Sharma')")
        t, pr = await paper(pg), await proot(pg); check("Ananya" not in t and "Ananya" not in pr and "Metformin" not in t, tag + "switch Ananya→Meera: nothing of Ananya remains (screen + print copy)")
        await pg.select_option("select[data-change=switchP]", "SYN-002"); await pg.wait_for_selector("#tab .paper:has-text('Kavya Reddy')"); t, pr = await paper(pg), await proot(pg)
        check("Meera" not in t and "Meera" not in pr and "Ananya" not in t, tag + "switch Meera→Kavya: no stale content")
        await open_brief(pg, base, "SYN-000"); await pg.evaluate("()=>{location.hash='#/p/SYN-001/export'}"); stale = await pg.evaluate("()=>document.querySelectorAll('#tab .paper').length&&document.querySelector('#tab .paper').innerText.includes('Ananya')")
        check(not stale, tag + "no stale Ananya brief remains visible right after navigating to Meera"); await pg.wait_for_selector("#tab .paper:has-text('Meera Sharma')")
        # late/out-of-order responses must not mix patients (only meaningful with real fetches)
        if not preview:
            B0 = await api(pg, "/api/patients/SYN-000/brief")
            async def slow(route): await asyncio.sleep(2.5); await route.continue_()
            await pg.route("**/api/patients/SYN-000/brief", slow); await ws(pg, base); await pg.goto(base + "#/p/SYN-000/export"); await pg.wait_for_selector("text=Generating the brief")
            await pg.select_option("select[data-change=switchP]", "SYN-002"); await pg.wait_for_selector("#tab .paper:has-text('Kavya Reddy')"); await pg.wait_for_timeout(3500)
            t, pr = await paper(pg), await proot(pg); check("Kavya Reddy" in t and "Ananya" not in t and "Ananya" not in pr and "Total Cholesterol" not in t, "late Ananya response arriving after switching to Kavya is discarded (no mixing)")
            await pg.unroute("**/api/patients/SYN-000/brief")
            # one section fails inside the server (test hook): flagged as 'could not be retrieved', never 'no records'; other sections stay Ananya's
            await pg.route("**/api/patients/SYN-000/brief", lambda r: r.continue_(url=r.request.url.split("?")[0] + "?simulate_failure=measurements"))
            await ws(pg, base); await open_brief(pg, base, "SYN-000"); t = await paper(pg)
            check("Could not be retrieved" in t and "clinical measurements section could not be built" in t and "No laboratory tests or vital-sign measurements are recorded" not in t and "Ananya Rao" in t and "3 May 2025" in t, "failed measurements section: 'could not be retrieved', not 'no records'; other sections intact")
            check("measurements could not be retrieved" in t and "0 verified measurements" not in t, "overview says 'could not be retrieved' (never '0 measurements')")
            check("Could not be retrieved: the clinical measurements section" in t, "the failure is also listed under missing information / review")
            await pg.unroute("**/api/patients/SYN-000/brief")
            # whole request fails: clear error, no report, no print control, no fallback content
            await pg.route("**/api/patients/SYN-000/brief", lambda r: r.fulfill(status=500, body='{"error":"boom"}', content_type="application/json"))
            await ws(pg, base); await pg.goto(base + "#/p/SYN-000/export"); await pg.wait_for_selector("text=The brief for SYN-000 could not be generated"); t = await pg.inner_text("#tab")
            check("boom" in t and await pg.locator("#tab .paper").count() == 0 and await pg.locator("[data-act=briefPrint]").count() == 0 and "Total Cholesterol" not in t and (await proot(pg)).strip() == "", "failed brief request: honest error, no report, no print button, nothing substituted")
            await pg.unroute("**/api/patients/SYN-000/brief")
            # mismatched identity: Meera's request answered with Ananya's brief -> rejected
            await pg.route("**/api/patients/SYN-001/brief", lambda r: r.fulfill(status=200, body=json.dumps(B0), content_type="application/json"))
            await ws(pg, base); await pg.goto(base + "#/p/SYN-001/export"); await pg.wait_for_selector("text=The brief for SYN-001 could not be generated"); t = await pg.inner_text("#tab")
            check("different patient and was rejected" in t and "Total Cholesterol" not in t and "214" not in t and "Ananya" not in t, "mismatched-patient brief is rejected, not displayed")
            await pg.unroute("**/api/patients/SYN-001/brief")
            # regenerate after a stored change
            await pg.evaluate("()=>fetch('/api/patients/SYN-001/measurements',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({test:'HbA1c',value:5.9,unit:'%',date:'2026-03-01',ref_low:4,ref_high:5.6})})")
            await ws(pg, base); await open_brief(pg, base, "SYN-001"); t = await paper(pg); rows = await pg.eval_on_selector_all("#tab .paper tbody tr", "e=>e.map(x=>x.innerText)")
            check(any("HbA1c" in r and "5.9" in r and "1 Mar 2026" in r and "4–5.6" in r for r in rows), "regenerated brief reflects the newly stored HbA1c (value, unit, date, range)")
            await open_brief(pg, base, "SYN-002"); check("HbA1c" not in await paper(pg), "…and Kavya's brief is unaffected")
            await ws(pg, base); await open_brief(pg, base, "SYN-001"); await pg.evaluate("()=>fetch('/api/patients/SYN-001/measurements',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({test:'Prolactin',value:11,unit:'ng/mL',date:'2026-03-02'})})")
            await pg.click("[data-act=briefRefresh]"); await pg.wait_for_selector("#tab .paper:has-text('Prolactin')", timeout=20000); check(True, "Regenerate button: a second stored change (Prolactin) appears after clicking Regenerate")
            resp = await pg.context.request.get(base + "api/patients/SYN-001/report.pdf"); check("Prolactin" in _txt(await resp.body()), "server PDF regenerated from the same stored change also contains Prolactin")
            async def cells_of(test_name, src_part):
                return await pg.evaluate("([n,sp])=>{const r=[...document.querySelectorAll('#tab .paper table.bt tbody tr')].find(x=>x.cells.length==8&&x.cells[0].innerText==n&&x.cells[7].innerText.includes(sp));return r?[...r.cells].map(c=>c.innerText):null}", [test_name, src_part])
            async def state_parity(label):
                await ws(pg, base); await open_brief(pg, base, "SYN-001"); _, data = await print_pdf(pg, "SYN-001", "/tmp/state_SYN-001.pdf"); await parity(pg, base, "SYN-001", data, f"[full app] Meera ({label})")
            # --- pending review: upload a lab report for Meera but do NOT approve it ---
            up = await pg.evaluate("async()=>{const b=await fetch('/api/samples/synthetic_lab_report.pdf',{credentials:'same-origin'}).then(r=>r.blob());return await fetch('/api/patients/SYN-001/documents?filename=lab.pdf',{method:'POST',body:b,credentials:'same-origin'}).then(r=>r.json())}")
            await ws(pg, base); await open_brief(pg, base, "SYN-001"); t = await paper(pg); RI = (await api(pg, "/api/patients/SYN-001/brief"))["review_items"]; G = {g["key"]: g["items"] for g in RI}
            check(len(up["fields"]) == 8 and any("8 values read from “lab.pdf”" in x and "HbA1c" in x for x in G["awaiting_review"]), "pending review: the 8 unapproved values appear under 'Awaiting your review' (payload)")
            check("Awaiting your review" in t and "8 values read from “lab.pdf” are awaiting your review and not used" in t, "pending review: shown in the on-screen brief")
            check(not any("awaiting" in x.lower() for x in G["not_recorded"] + G["unavailable"] + G["data_quality"]) and any("Nothing recorded for “Lipid”" in x for x in G["not_recorded"]), "missing categories are labelled 'Not recorded' and never as awaiting review")
            check(await cells_of("HbA1c", "lab.pdf") is None, "pending values are NOT shown as measurements")
            await state_parity("pending review")
            await open_brief(pg, base, "SYN-002"); check("Awaiting your review" not in await paper(pg), "Kavya (no uploads) has no awaiting-review group")
            # --- approve: flags appear, SEPARATE from the reference range ---
            await pg.evaluate("async(ids)=>{for(const id of ids){await fetch('/api/fields/'+id,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'approve',update:false})})}}", [f["id"] for f in up["fields"]])
            await ws(pg, base); await open_brief(pg, base, "SYN-001"); t = await paper(pg)
            check("Awaiting your review" not in t, "after approval the awaiting-review group disappears")
            hb = await cells_of("HbA1c", "lab.pdf"); ts = await cells_of("Total Testosterone", "lab.pdf"); man = await cells_of("HbA1c", "manual entry")
            check(hb is not None and hb[4] == "4–5.6 %" and hb[5] == "High" and "High" not in hb[4], f"HbA1c from report: range and lab flag are separate cells ({hb[4:6] if hb else None})")
            check(ts is not None and ts[4] == "15–70 ng/dL" and ts[5] == "not provided", f"no flag in the source → 'not provided' (none invented) ({ts[4:6] if ts else None})")
            check(man is not None and man[5] == "not provided", "manually entered HbA1c carries no flag")
            flagged = await pg.evaluate("()=>[...document.querySelectorAll('#tab .paper table.bt tbody tr')].filter(x=>x.cells.length==8&&x.cells[5].innerText=='High').map(x=>x.cells[0].innerText)")
            src_flags = sorted(f["flag"] for f in up["fields"] if f["flag"]); check(len(flagged) == len(src_flags) == 5, f"exactly the {len(src_flags)} values the report flagged 'High' show a flag ({flagged})")
            await state_parity("approved, with lab flags")
            # --- genuine failures must be 'unavailable', never turned into 'no records' / 'nothing awaiting review' ---
            for key, must_not in (("review", "awaiting"), ("quality", "Nothing recorded for")):
                def mk(k): return lambda r: r.continue_(url=r.request.url.split("?")[0] + f"?simulate_failure={k}")
                await pg.route("**/api/patients/SYN-001/brief", mk(key))
                await ws(pg, base); await open_brief(pg, base, "SYN-001"); RI2 = {g["key"]: g["items"] for g in (await api(pg, "/api/patients/SYN-001/brief?simulate_failure=" + key))["review_items"]}; t = await paper(pg)
                lab = {"review": "the review items section", "quality": "the data-quality checks section"}[key]
                check(any(lab in x for x in RI2["unavailable"]) and f"Could not be retrieved: {lab}" in t, f"simulated '{key}' failure is listed under 'Unavailable'")
                check(not any(must_not in x for g in ("not_recorded", "data_quality") for x in RI2[g]), f"…and is not misreported as {must_not!r} (no false 'none' claim)")
                await pg.unroute("**/api/patients/SYN-001/brief")
        check(len(errs) == 0, tag + f"no JavaScript errors ({errs[:2]})"); await b.close()
async def regress(base):
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(); bad = []
        await pg.goto(base); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        for pid in NAMES:
            for tab in ["overview", "since", "twin", "predict", "quality", "timeline", "reports", "tests", "cycle", "symptoms", "physio", "export"]:
                await pg.goto(base + f"#/p/{pid}/{tab}"); await pg.wait_for_selector(".pbar"); await pg.wait_for_timeout(250)
                if await pg.locator("text=Something went wrong").count(): bad.append((pid, tab))
        check(not bad, f"regression: all 12 pages load for all 3 patients without error panels {bad}"); await b.close()
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port), "NAARI_TEST_HOOKS": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(run(B, False)); asyncio.run(regress(B))
    subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL)
    html = open("/mnt/user-data/outputs/naari_preview.html").read()
    check("application/pdf;base64" not in html and "NAARI_SYN-000_report.pdf" not in html, "[preview] the fixed Ananya PDF (data URI / hard-coded filename) is gone from the HTML")
    asyncio.run(run("file:///mnt/user-data/outputs/naari_preview.html", True))
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL BRIEF CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
