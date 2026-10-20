"""Final audit (automated parts): raw technical keys, placeholders, accessible names, headings, table headers, focus indicator, keyboard reach, tour dialog semantics, contrast,
horizontal overflow at 375 / 768 / 1280 px. Live app (after Ananya's demo workflow) + static preview, 3 patients x 12 tabs + landing + workspace. Chromium.
This is a programmatic audit, NOT a screen-reader session or an accessibility certification. Run: python3 tests/test_audit.py"""
import os, sys, re, json, time, glob, subprocess, tempfile, asyncio, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}/"; PV = "file:///mnt/user-data/outputs/naari_preview.html"; FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
TABS = ["overview", "since", "twin", "predict", "quality", "timeline", "reports", "tests", "cycle", "symptoms", "physio", "export"]; PIDS = ["SYN-000", "SYN-001", "SYN-002"]
RAW = re.compile(r"\b(cgm|clinical)\.[A-Za-z_0-9]|_pct\b|hours_since")
PLACE = re.compile(r"\[(Team name|College name|team|college|name / email / phone|to be added)[^\]]*\]", re.I)
AUDIT_JS = r"""()=>{const out={names:[],h:[],th:[],img:[],ovf:document.documentElement.scrollWidth-window.innerWidth,contrast:[],raw:[],place:[]};
 const vis=e=>{const r=e.getClientRects();return r.length&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none'};
 const name=e=>{let n=(e.getAttribute('aria-label')||'').trim();if(n)return n;const lb=e.getAttribute('aria-labelledby');if(lb){n=lb.split(/\s+/).map(i=>(document.getElementById(i)||{}).textContent||'').join(' ').trim();if(n)return n}
   if(e.labels&&e.labels.length){n=[...e.labels].map(l=>l.textContent).join(' ').trim();if(n)return n}if(e.closest('label')){n=e.closest('label').textContent.trim();if(n)return n}
   n=(e.textContent||'').trim();if(n&&!/^(INPUT|SELECT|TEXTAREA)$/.test(e.tagName))return n;n=(e.getAttribute('title')||'').trim();if(n)return n;if(e.tagName==='INPUT'&&e.type==='submit')return e.value;return ''};
 document.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=tab]').forEach(e=>{if(vis(e)&&!name(e))out.names.push(e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+(e.name?'[name='+e.name+']':'')+(e.className?'.'+String(e.className).split(' ')[0]:''))});
 let last=0;document.querySelectorAll('h1,h2,h3,h4').forEach(e=>{if(!vis(e))return;const l=+e.tagName[1];if(last&&l>last+1)out.h.push(`h${last}→h${l} "${e.textContent.trim().slice(0,30)}"`);last=l});
 out.hcount=[...document.querySelectorAll('h1,h2')].filter(vis).length;
 document.querySelectorAll('table').forEach(t=>{if(vis(t)&&!t.querySelector('th'))out.th.push((t.id||'table'))});
 document.querySelectorAll('img').forEach(i=>{if(vis(i)&&!i.hasAttribute('alt'))out.img.push(i.className||'img')});
 const lum=c=>{const [r,g,b]=c.map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return .2126*r+.7152*g+.0722*b};
 const parse=s=>{const m=s.match(/rgba?\(([^)]+)\)/);if(!m)return null;const p=m[1].split(',').map(x=>parseFloat(x));return {c:p.slice(0,3),a:p.length>3?p[3]:1}};
 const bg=e=>{let x=e;const stack=[];while(x){const p=parse(getComputedStyle(x).backgroundColor);if(p&&p.a>0){stack.push(p);if(p.a>=.99)break}x=x.parentElement}let base=[255,255,255];for(let i=stack.length-1;i>=0;i--){const p=stack[i];base=base.map((b,k)=>b*(1-p.a)+p.c[k]*p.a)}return base};
 const seen=new Set();const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){const e=n.parentElement;if(!e||!n.textContent.trim()||['SCRIPT','STYLE'].includes(e.tagName)||!vis(e)||e.closest('svg')||e.closest('[disabled]')||e.closest('[aria-disabled=true]'))continue;
   const cs=getComputedStyle(e),fg=parse(cs.color);if(!fg)continue;const b=bg(e),f=fg.a<1?fg.c.map((v,k)=>v*fg.a+b[k]*(1-fg.a)):fg.c,L1=lum(f),L2=lum(b),r=(Math.max(L1,L2)+.05)/(Math.min(L1,L2)+.05),sz=parseFloat(cs.fontSize),bold=parseInt(cs.fontWeight)>=700,large=sz>=24||(sz>=18.66&&bold);
   if(r<(large?3:4.5)){const key=e.tagName+'.'+String(e.className).split(' ')[0]+'|'+cs.color+'|'+r.toFixed(2);if(!seen.has(key)){seen.add(key);out.contrast.push(`${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]} ${cs.color} on rgb(${b.map(Math.round)}) = ${r.toFixed(2)}:1 "${n.textContent.trim().slice(0,24)}"`)}}}
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);while(n=walker.nextNode()){const e=n.parentElement;if(!e||['SCRIPT','STYLE'].includes(e.tagName))continue;if(e.closest('details')&&!e.closest('summary'))continue;const s=n.textContent;if(/\b(cgm|clinical)\.[A-Za-z_0-9]|_pct\b|hours_since/.test(s))out.raw.push(s.trim().slice(0,60));const m=s.match(/\[(Team name|College name|team|college|name \/ email \/ phone|to be added)[^\]]*\]/i);if(m)out.place.push(m[0])}
 return out}"""
async def go(pg, pid, tab):
    await pg.evaluate("h=>{location.hash=h}", f"#/p/{pid}/{tab}"); await pg.wait_for_selector("#tab .card, #tab .empty, #tab .ph, #tab table", timeout=15000); await pg.wait_for_timeout(350)
async def sweep(pg, label, widths):
    agg = {"names": set(), "h": set(), "th": set(), "img": set(), "raw": set(), "place": set(), "contrast": set(), "ovf": []}; pages = 0
    for w, h in widths:
        await pg.set_viewport_size({"width": w, "height": h})
        for pid in PIDS:
            for t in TABS:
                await go(pg, pid, t); r = await pg.evaluate(AUDIT_JS); pages += 1
                for k in ("names", "h", "th", "img", "raw", "place"): agg[k].update(f"{pid}/{t}: {x}" for x in r[k])
                if w == 1280: agg["contrast"].update(r["contrast"])
                if r["ovf"] > 1: agg["ovf"].append(f"{w}px {pid}/{t} +{r['ovf']}px")
        for where in ("#/workspace",):
            await pg.evaluate("h=>{location.hash=h}", where); await pg.wait_for_selector("#pl a.card"); await pg.wait_for_timeout(300); r = await pg.evaluate(AUDIT_JS); pages += 1
            for k in ("names", "h", "raw", "place"): agg[k].update(f"workspace: {x}" for x in r[k])
            if r["ovf"] > 1: agg["ovf"].append(f"{w}px workspace +{r['ovf']}px")
            if w == 1280: agg["contrast"].update(r["contrast"])
    check(not agg["raw"], f"[{label}] no raw technical field names outside 'Technical details' on {pages} pages {sorted(agg['raw'])[:2]}")
    check(not agg["place"], f"[{label}] no [Team name]/[College name]-style placeholders on any page {sorted(agg['place'])[:2]}")
    check(not agg["names"], f"[{label}] every visible control has an accessible name {sorted(agg['names'])[:6]}")
    check(not agg["th"], f"[{label}] every table has header cells {sorted(agg['th'])[:3]}")
    check(not agg["img"], f"[{label}] every image has an alt attribute {sorted(agg['img'])[:3]}")
    check(not agg["h"], f"[{label}] no skipped heading levels {sorted(agg['h'])[:4]}")
    check(not agg["contrast"], f"[{label}] text contrast >= 4.5:1 (3:1 large text) at 1280px {sorted(agg['contrast'])[:5]}")
    check(not agg["ovf"], f"[{label}] no horizontal page overflow at {[w for w, _ in widths]} px {agg['ovf'][:4]}")
async def keyboard(pg, label):
    await pg.set_viewport_size({"width": 1280, "height": 900}); await go(pg, "SYN-000", "overview")
    await pg.evaluate("document.activeElement&&document.activeElement.blur();window.scrollTo(0,0)"); seen = []; vis_ok = []
    for _ in range(14):
        await pg.keyboard.press("Tab"); d = await pg.evaluate("""()=>{const e=document.activeElement,cs=getComputedStyle(e);return {tag:e.tagName,txt:(e.innerText||e.getAttribute('aria-label')||e.value||'').slice(0,30),ring:(cs.outlineStyle!=='none'&&parseFloat(cs.outlineWidth)>0)||cs.boxShadow!=='none'}}"""); seen.append(d["tag"] + ":" + d["txt"]); vis_ok.append(d["ring"])
    check(len(set(seen)) >= 8, f"[{label}] Tab moves through distinct controls ({len(set(seen))} distinct in 14 presses)")
    check(all(vis_ok), f"[{label}] every focused control shows a visible focus indicator ({sum(vis_ok)}/{len(vis_ok)})")
    await go(pg, "SYN-000", "overview"); await pg.focus("[data-act=tourStart]"); await pg.keyboard.press("Enter"); await pg.wait_for_selector("#tour", timeout=10000); await pg.wait_for_timeout(600)
    t = await pg.evaluate("()=>{const t=document.querySelector('#tour');return {role:t.getAttribute('role'),label:t.getAttribute('aria-label')||t.getAttribute('aria-labelledby'),live:t.getAttribute('aria-live'),focusIn:t.contains(document.activeElement)}}")
    check(t["role"] in ("dialog", "region", "complementary") and bool(t["label"]), f"[{label}] guided tour card has a role and an accessible name {t}")
    await pg.keyboard.press("Escape"); await pg.wait_for_timeout(400); check(await pg.locator("#tour").count() == 0, f"[{label}] Escape closes the guided tour (keyboard users are never trapped)")
    await go(pg, "SYN-000", "twin"); await pg.locator("#ca").focus(); await pg.keyboard.press("Tab"); await pg.keyboard.press("Tab"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(300); check("compared with" in (await pg.inner_text("#cmp")), f"[{label}] Twin comparison can be run from the keyboard alone")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1280, "height": 900}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B); await pg.click("button:has-text('Open Demo Workspace') >> nth=0"); await pg.wait_for_selector("text=Choose a patient")
        await pg.evaluate("""async()=>{const png=await fetch('/api/samples/synthetic_lab_report_scan.png',{credentials:'same-origin'}).then(r=>r.blob());const d=await fetch('/api/patients/SYN-000/documents?filename=synthetic_lab_report_scan.png',{method:'POST',credentials:'same-origin',body:png}).then(r=>r.json());const H={'Content-Type':'application/json'};
          for(let i=0;i<d.fields.length;i++){const f=d.fields[i];await fetch('/api/fields/'+f.id,{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({action:'approve',edits:/^hba/i.test(f.name)?{name:'HbA1c'}:{},update:i==d.fields.length-1})})}
          const csv=await fetch('/api/samples/demo_new_cgm_batch.csv',{credentials:'same-origin'}).then(r=>r.blob());await fetch('/api/patients/SYN-000/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2',{method:'POST',credentials:'same-origin',body:csv});
          await fetch('/api/patients/SYN-000/twin/update',{method:'POST',credentials:'same-origin',headers:H,body:JSON.stringify({reason:'imported demo_new_cgm_batch.csv (3,456 new data points)'})})}""")
        W = [(1280, 900), (768, 1024), (375, 800)]
        await sweep(pg, "full app", W); await keyboard(pg, "full app"); check(not errs, f"[full app] no JavaScript errors {errs[:2]}")
        # landing page (signed out) placeholders / names
        lp = await b.new_page(viewport={"width": 1280, "height": 900}); await lp.goto(B); await lp.wait_for_timeout(500); r = await lp.evaluate(AUDIT_JS); check(not r["place"] and not r["names"] and r["ovf"] <= 1 and not r["contrast"], f"[landing page] placeholders {r['place']}, unnamed controls {r['names']}, overflow {r['ovf']}, contrast {r['contrast'][:2]}")
        subprocess.run([sys.executable, "build_preview.py"], cwd=ROOT, stdout=subprocess.DEVNULL); pv = await b.new_page(viewport={"width": 1280, "height": 900}); perr = []; pv.on("pageerror", lambda e: perr.append(str(e)))
        await pv.goto(PV); await pv.click("button:has-text('Open Demo Workspace') >> nth=0"); await pv.wait_for_selector("text=Choose a patient"); await sweep(pv, "static preview", W); await keyboard(pv, "static preview")
        bn = await pv.evaluate("()=>{const b=document.querySelector('.banner');return {h:b.getBoundingClientRect().height,t:b.innerText}}"); check(bn["h"] < 70 and "READ-ONLY" in bn["t"].upper(), f"[static preview] read-only banner is present and compact ({bn['h']:.0f}px high)")
        check(not perr, f"[static preview] no JavaScript errors {perr[:2]}"); await b.close()
    # project files: no unconfigured placeholders
    hits = []
    for f in glob.glob(f"{ROOT}/**/*", recursive=True):
        if os.path.isfile(f) and f.endswith((".md", ".py", ".js", ".html", ".txt", ".json")) and "__pycache__" not in f and "/tests/" not in f:
            try: s = open(f, encoding="utf-8").read()
            except Exception: continue
            if PLACE.search(s): hits.append(os.path.relpath(f, ROOT))
    check(not hits, f"[project files] no unconfigured [Team name]/[College name]-style placeholders in source or docs {hits}")
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "api/health"); break
        except Exception: time.sleep(.5)
    asyncio.run(main())
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL AUDIT CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
