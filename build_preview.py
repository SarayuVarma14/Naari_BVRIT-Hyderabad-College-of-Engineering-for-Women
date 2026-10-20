"""Builds a static, read-only preview (naari_preview.html): runs the real demo flow on a temp DB, snapshots every GET the UI makes."""
import os, sys, json, base64, subprocess, tempfile, time, urllib.request, sqlite3
ROOT = os.path.dirname(os.path.abspath(__file__)); tmp = tempfile.mkdtemp(); port = 8812
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/p.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
B = f"http://127.0.0.1:{port}"; ck = {}
def call(path, method="GET", data=None, raw=None, binary=False):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else (b"{}" if method == "POST" else None))
    r = urllib.request.Request(B + path, data=body, method=method, headers={"Cookie": ck.get("c", ""), "Content-Type": "application/json"} if raw is None else {"Cookie": ck.get("c", "")})
    with urllib.request.urlopen(r) as x:
        if x.headers.get("Set-Cookie"): ck["c"] = x.headers["Set-Cookie"].split(";")[0]
        b = x.read(); return b if binary else json.loads(b)
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "/api/health"); break
        except Exception: time.sleep(.5)
    call("/api/demo-login", "POST"); P = "/api/patients/SYN-000"
    d = call(P + "/documents?filename=synthetic_lab_report_scan.png", "POST", raw=open(f"{ROOT}/data/samples/synthetic_lab_report_scan.png", "rb").read())
    for i, f in enumerate(d["fields"]):
        call(f"/api/fields/{f['id']}", "POST", {"action": "approve", "edits": {"name": "HbA1c"} if f["name"].lower().startswith("hba") else {}, "update": i == len(d["fields"]) - 1})
    pr = call(P + "/predict", "POST", {"as_of": "2026-03-10T23:55:00Z"})
    call(P + "/timeseries?filename=demo_new_cgm_batch.csv&batch=demo-batch-2", "POST", raw=open(f"{ROOT}/data/samples/demo_new_cgm_batch.csv", "rb").read())
    call(P + "/twin/update", "POST", {"reason": "imported demo_new_cgm_batch.csv (3,456 new data points)"}); call(f"/api/predictions/{pr['id']}/outcome", "POST")
    D = {"/api/patients": call("/api/patients"), "/api/model": call("/api/model"), "/api/documents/1": call("/api/documents/1")}
    for pid in ("SYN-000", "SYN-001", "SYN-002"):
        for w in ("summary", "timeline", "measurements", "cycles", "symptoms", "documents", "twin", "predictions", "journey", "quality", "since", "cycles/summary", "brief"): D[f"/api/patients/{pid}/{w}"] = call(f"/api/patients/{pid}/{w}")
    vs = [s["version"] for s in D["/api/patients/SYN-000/twin"]]
    for a in vs:
        for b in vs: D[f"/api/patients/SYN-000/twin/compare?a={a}&b={b}"] = call(f"/api/patients/SYN-000/twin/compare?a={a}&b={b}")
    db = sqlite3.connect(f"{tmp}/p.db"); G = {pid: [[r[0], r[1], r[2]] for r in db.execute("SELECT ts,value,quality FROM timeseries WHERE patient=? AND metric='glucose' ORDER BY ts", (pid,))] for pid in ("SYN-000", "SYN-001", "SYN-002")}
    png = base64.b64encode(open(f"{ROOT}/data/samples/synthetic_lab_report_scan.png", "rb").read()).decode()
finally: srv.terminate()
shim = """<script>
window.NAARI_PREVIEW=true;
const DATA=%s,GLU=%s,LOGGED0=false;let LOGGED=false;const J=(o,s=200)=>new Response(JSON.stringify(o),{status:s,headers:{'Content-Type':'application/json'}});
const pvMON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],pvDd=(a,b)=>Math.round((Date.parse(b)-Date.parse(a))/864e5),pvAdd=(a,n)=>new Date(Date.parse(a)+n*864e5).toISOString().slice(0,10),pvIsD=x=>/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(x||'')&&!isNaN(Date.parse(x)),pvDf=x=>{const t=new Date(Date.parse(x));return t.getUTCDate()+' '+pvMON[t.getUTCMonth()]+' '+t.getUTCFullYear()},pvR2=v=>Math.round(v*100)/100,pvR3=v=>Math.round(v*1000)/1000,pvMean=a=>a.reduce((s,x)=>s+x,0)/a.length;
/* since(): the same comparison as the server, computed ONLY from data embedded in this file (nothing is invented; wearable counts are not available for custom dates) */
function sinceLocal(pid,frm,to){const pre='/api/patients/'+pid,base=DATA[pre+'/since'];if(!base||base.empty)return [base||{empty:true},200];const ref=base.reference_date;to=to||ref;frm=frm||pvAdd(ref,-90);
 if(!pvIsD(frm)||!pvIsD(to)||pvDd(frm,to)<=0)return [{error:'Choose two valid dates (YYYY-MM-DD) with the first earlier than the second'},422];
 const span=pvDd(frm,to),pf=pvAdd(frm,-span),R={from:frm,to:to,reference_date:ref,prev_from:pf,physiology_limited:true},inw=d=>d>frm&&d<=to,ms=(DATA[pre+'/measurements']||[]).filter(m=>m.verified).map((m,i)=>[m,i]).sort((a,b)=>a[0].date<b[0].date?-1:a[0].date>b[0].date?1:a[1]-b[1]).map(x=>x[0]);
 R.new_measurements=ms.filter(m=>inw(m.date)).map(m=>({label:'New',test:m.test,value:m.value,unit:m.unit,date:m.date,source:m.source}));const by=new Map();ms.forEach(m=>{const k=m.test.toLowerCase();if(!by.has(k))by.set(k,[]);by.get(k).push(m)});R.changed_measurements=[];
 by.forEach(v=>{const b=v.filter(m=>m.date<=frm),a=v.filter(m=>inw(m.date));if(a.length&&b.length){const x=b[b.length-1],y=a[a.length-1];if(x.unit!==y.unit)R.changed_measurements.push({label:'Insufficient data',test:y.test,note:'units differ ('+x.unit+' vs '+y.unit+'); not compared',before:x,after:y});else R.changed_measurements.push({label:x.value!==y.value?'Changed':'Unchanged',test:y.test,unit:y.unit,before:{value:x.value,date:x.date},after:{value:y.value,date:y.date},delta:pvR3(y.value-x.value)})}});
 const cy=(DATA[pre+'/cycles']||[]).slice().sort((a,b)=>a.start<b.start?-1:1);R.cycles=cy.map((x,i)=>({label:'New',start:x.start,end:x.end,interval_from_previous:i?pvDd(cy[i-1].start,x.start):null,_in:inw(x.start)})).filter(x=>x._in).map(x=>{delete x._in;return x});
 const sy=DATA[pre+'/symptoms']||[];R.symptoms=[];[...new Set(sy.map(s=>s.kind))].sort().forEach(k=>{const cur=sy.filter(s=>s.kind===k&&inw(s.date)).map(s=>s.severity),pr=sy.filter(s=>s.kind===k&&s.date>pf&&s.date<=frm).map(s=>s.severity);
  if(cur.length&&pr.length)R.symptoms.push({label:pvMean(cur)!==pvMean(pr)?'Changed':'Unchanged',kind:k,before_mean:pvR2(pvMean(pr)),after_mean:pvR2(pvMean(cur)),n_before:pr.length,n_after:cur.length});else if(cur.length)R.symptoms.push({label:'Insufficient data',kind:k,note:cur.length+' new entr'+(cur.length==1?'y':'ies')+', but none in the earlier comparison window ('+pvDf(pf)+' to '+pvDf(frm)+')'})});
 R.physiology=[];const T=(DATA[pre+'/twin']||[]).slice().sort((a,b)=>a.version-b.version);R.twin={versions_in_window:T.filter(s=>inw(s.as_of.slice(0,10))).map(s=>({version:s.version,as_of:s.as_of,reason:s.reason,n_changes:s.changes.length})),version_at_start:(T.filter(s=>s.as_of.slice(0,10)<=frm).pop()?{version:T.filter(s=>s.as_of.slice(0,10)<=frm).pop().version,as_of:T.filter(s=>s.as_of.slice(0,10)<=frm).pop().as_of}:null)};
 R.awaiting_review=((DATA[pre+'/summary']||{}).pending_reviews||[]).map(x=>({label:'Awaiting review',doc:x.id,filename:x.filename,n:x.n}));return [R,200]}
window.fetch=async(u,o={})=>{const m=(o.method||'GET').toUpperCase(),url=new URL(u,'http://x'),p=url.pathname;
 if(m==='POST'){if(p==='/api/demo-login'||p==='/api/logout'){LOGGED=p==='/api/demo-login';return J({user:'clinician'})}return J({error:'Unavailable in the static preview. Open the full application to perform this action.'},403)}
 if(p==='/api/me')return J({user:LOGGED?'clinician':null});if(!LOGGED)return J({error:'Sign in required'},401);
 const s=p.match(/^\\/api\\/patients\\/([\\w-]+)\\/series$/);if(s){const q=url.searchParams,a=q.get('from')||'',b=q.get('to')||'~',pts=(GLU[s[1]]||[]).filter(r=>r[0]>=a&&r[0]<=b);return J({metric:'glucose',unit:'mg/dL',points:pts,n:pts.length})}
 if(p.endsWith('/since')&&url.search){const q=url.searchParams,r=sinceLocal(p.split('/')[3],q.get('from'),q.get('to'));return J(r[0],r[1])}const k=p+(p.endsWith('/compare')?url.search:'');return DATA[k]!==undefined?J(DATA[k]):J({error:'Not available in the static preview (needs the local server).'},404)};
</script>""" % (json.dumps(D), json.dumps(G))
h = open(f"{ROOT}/web/index.html").read()
h = h.replace('<script src="/ui2.js"></script>', "<script>" + open(f"{ROOT}/web/ui2.js").read() + "</script>")
h = h.replace("Research prototype · Seeded demo data is synthetic · Not clinically validated", "STATIC READ-ONLY PREVIEW of the real NAARI UI · snapshot after the full demo · Synthetic data · Not clinically validated")
h = h.replace('<main id="app">', shim + '<main id="app">') if False else h.replace("<script>\nconst $=", shim + "<script>\nconst $=", 1)
h = h.replace('<img class="pv" src="/api/documents/${id}/file"', '<img class="pv" src="data:image/png;base64,' + png + '"')
assert "STATIC READ-ONLY" in h and "const DATA=" in h and png[:20] in h
os.makedirs("/mnt/user-data/outputs", exist_ok=True); open("/mnt/user-data/outputs/naari_preview.html", "w").write(h); print("built", len(h)//1024, "KB")
