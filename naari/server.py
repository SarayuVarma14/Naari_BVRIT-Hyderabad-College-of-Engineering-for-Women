"""NAARI local research-prototype server (stdlib only). Run: python3 -m naari.server"""
import io, json, os, re, secrets, sqlite3, hmac, uuid, statistics, datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import pandas as pd
from .twin import Twin, RANGE, UNIT
from . import records, extract, seed as seedmod
ROOT = seedmod.ROOT; DB = os.environ.get("NAARI_DB", f"{ROOT}/naari.db"); UP = f"{ROOT}/uploads"
MODEL = f"{ROOT}/models/glucose_tar_2h.joblib"; MAXB = 10*1024*1024; SESSIONS = {}
KINDS = {"pdf": "pdf", "png": "png", "jpg": "jpg", "jpeg": "jpg", "csv": "csv", "json": "json"}
MAGIC = {"pdf": b"%PDF", "png": b"\x89PNG", "jpg": b"\xff\xd8\xff"}
class Err(Exception):
    def __init__(s, code, msg): s.code, s.msg = code, msg
def T():
    t = Twin(DB, MODEL); records.init(t.db); t.db.row_factory = sqlite3.Row; return t
def rows(t, q, a=()): return [dict(r) for r in t.db.execute(q, a).fetchall()]
def need_patient(t, pid):
    if not t.db.execute("SELECT 1 FROM patients WHERE id=?", (pid,)).fetchone(): raise Err(404, "Unknown patient")
def default_as_of(t, pid):
    a = t.db.execute("SELECT MAX(ts) FROM timeseries WHERE patient=? AND metric='glucose' AND quality='ok'", (pid,)).fetchone()[0]
    m = t.db.execute("SELECT MAX(date) FROM measurements WHERE patient=? AND verified=1", (pid,)).fetchone()[0]
    c = [x for x in (a, m and m + "T23:59:59Z") if x]
    return max(c) if c else dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def utc(s): return pd.Timestamp(s).tz_convert("UTC") if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC")
def flat(s):
    return {**{f"clinical.{k}": v["value"] for k, v in s["clinical"].items()}, **{f"cgm.{k}": v for k, v in s["cgm"].items()}}
def do_update(t, pid, reason):
    ver, ch = t.update(pid, default_as_of(t, pid), reason)
    return {"version": ver, "changes": ch, "unchanged": t.unchanged, "readiness": readiness(t, pid)}
def readiness(t, pid):
    a = t.db.execute("SELECT MAX(ts) FROM timeseries WHERE patient=? AND metric='glucose'", (pid,)).fetchone()[0]
    if not a: return {"available": False, "reasons": ["no glucose time-series imported"], "as_of": None}
    r = t.predict(pid, a, dry=True); r["as_of"] = a; return r
# ---------- time-series parsing ----------
def parse_series(raw):
    try: df = pd.read_csv(io.BytesIO(raw))
    except Exception as e: raise Err(400, f"Could not read CSV: {e}")
    tcol = next((c for c in df.columns if c.lower() in ("timestamp", "ts", "time", "datetime")), None)
    if not tcol: raise Err(400, "CSV needs a 'timestamp' column")
    issues = []; s = df[tcol].astype(str)
    if not s.str.contains(r"Z$|[+-]\d\d:?\d\d$", regex=True).any(): issues.append("No timezone in timestamps - interpreted as UTC.")
    ts = pd.to_datetime(df[tcol], utc=True, errors="coerce"); bad = int(ts.isna().sum())
    if "glucose_mmol" in df.columns and "glucose" not in df.columns:
        df["glucose"] = pd.to_numeric(df["glucose_mmol"], errors="coerce")*18.016; issues.append("glucose_mmol converted to mg/dL (x18.016); original mmol/L values are in your file.")
    mets = [m for m in RANGE if m in df.columns]
    if not mets: raise Err(400, f"No supported metric column. Supported: {', '.join(RANGE)} (glucose_mmol also accepted)")
    out = pd.DataFrame({"ts": ts}); 
    for m in mets: out[m] = pd.to_numeric(df[m], errors="coerce")
    out = out[ts.notna()].sort_values("ts")
    return out, mets, bad, issues
def preview_series(t, pid, raw):
    out, mets, bad, issues = parse_series(raw); rep = {"rows": len(out), "invalid_timestamps": bad, "metrics": {}, "issues": issues}
    for m in mets:
        lo, hi = RANGE[m]; v = out[m]
        ex = {r[0] for r in t.db.execute("SELECT ts FROM timeseries WHERE patient=? AND metric=? AND ts>=? AND ts<=?", (pid, m, out.ts.min().strftime("%Y-%m-%dT%H:%M:%SZ"), out.ts.max().strftime("%Y-%m-%dT%H:%M:%SZ")))}
        keys = set(out.ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ")[v.notna()])
        rep["metrics"][m] = {"unit": UNIT[m], "n": int(v.notna().sum()), "missing": int(v.isna().sum()), "out_of_range": int(((v < lo) | (v > hi)).sum()), "range_checked": [lo, hi], "already_stored_duplicates": len(keys & ex)}
    if "glucose" in mets:
        d = out.ts[out.glucose.notna()].diff().dt.total_seconds()/60; rep["glucose_gaps_over_15min"] = int((d > 15).sum()); rep["longest_gap_min"] = float(d.max()) if len(d) > 1 else 0
    rep["from"], rep["to"] = (out.ts.min().isoformat(), out.ts.max().isoformat()) if len(out) else (None, None)
    return rep
# ---------- documents ----------
def upload(t, pid, filename, body):
    need_patient(t, pid); ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    if ext not in KINDS: raise Err(415, f"Unsupported file type '.{ext}'. Allowed: pdf, png, jpg, jpeg, csv, json")
    if not body: raise Err(400, "Empty file")
    if len(body) > MAXB: raise Err(413, "File exceeds 10 MB limit")
    kind = KINDS[ext]
    if kind in MAGIC and not body.startswith(MAGIC[kind]): raise Err(415, "File content does not match its extension")
    stored = f"{uuid.uuid4().hex}.{kind}"; os.makedirs(UP, exist_ok=True); path = os.path.join(UP, stored)
    open(path, "wb").write(body); safe = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(filename))[:120]
    if kind in ("csv", "json"):
        try:
            d = pd.read_csv(io.BytesIO(body)) if kind == "csv" else pd.DataFrame(json.loads(body))
            d.columns = [c.lower() for c in d.columns]; miss = {"test", "value"} - set(d.columns)
            if miss: raise ValueError(f"missing columns: {sorted(miss)} (expected test,value,unit,date,ref_low,ref_high,flag)")
            fs = [{"page": 1, "name": str(r.get("test")), "value": str(r.get("value")), "unit": r.get("unit"), "date": r.get("date"), "ref_low": r.get("ref_low"), "ref_high": r.get("ref_high"), "flag": r.get("flag"), "method": "structured-file", "conf": None, "raw": None} for r in d.to_dict("records")]
            res = {"text": "", "pages": 1, "method": "structured-file", "fields": fs, "error": None}
        except Exception as e: res = {"text": "", "pages": 0, "method": None, "fields": [], "error": f"Could not parse structured file: {e}"}
    else: res = extract.extract(path, kind)
    cur = t.db.execute("INSERT INTO documents(patient,filename,stored,kind,status,method,error,text,pages) VALUES(?,?,?,?,?,?,?,?,?)", (pid, safe, stored, kind, "failed" if res["error"] else "extracted", res["method"], res["error"], res["text"], res["pages"]))
    did = cur.lastrowid
    for f in res["fields"]:
        t.db.execute("INSERT INTO fields(document_id,page,name,value,unit,date,ref_low,ref_high,flag,method,conf,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (did, f["page"], f["name"], f["value"], f["unit"], f["date"], f["ref_low"], f["ref_high"], f["flag"], f["method"], f["conf"], f["raw"]))
    t.db.execute("INSERT INTO audit(at,action,detail) VALUES(datetime('now'),'upload',?)", (json.dumps({"doc": did, "kind": kind, "fields": len(res["fields"])}),)); t.db.commit()
    return get_doc(t, did)
def get_doc(t, did):
    d = rows(t, "SELECT id,patient,filename,kind,uploaded_at,status,method,error,text,pages FROM documents WHERE id=?", (did,))
    if not d: raise Err(404, "Unknown document")
    d = d[0]; d["fields"] = rows(t, "SELECT * FROM fields WHERE document_id=? ORDER BY id", (did,)); return d
def approve_field(t, fid, edits, update=True):
    f = rows(t, "SELECT * FROM fields WHERE id=?", (fid,))
    if not f: raise Err(404, "Unknown field")
    f = f[0]; 
    if f["status"] == "approved": raise Err(409, "Already approved")
    for k in ("name", "value", "unit", "date", "ref_low", "ref_high", "flag"):
        if k in edits: f[k] = (str(edits[k]).strip() or None) if edits[k] is not None else None
    try: val = float(f["value"])
    except Exception: raise Err(422, "Result must be a number - correct it before approving")
    if not f["name"] or not f["unit"]: raise Err(422, "Test name and unit are required (never guessed)")
    try: dt.date.fromisoformat(f["date"] or "")
    except Exception: raise Err(422, "A valid test date (YYYY-MM-DD) is required (never guessed)")
    doc = rows(t, "SELECT * FROM documents WHERE id=?", (f["document_id"],))[0]
    num = lambda x: float(x) if x not in (None, "") else None
    ok = t.add_measurement(doc["patient"], f["name"], val, f["unit"], f["date"], f"{doc['filename']} p.{f['page']}", True, category=extract.category(f["name"]), ref_low=num(f["ref_low"]), ref_high=num(f["ref_high"]), flag=f["flag"], document_id=doc["id"], page=f["page"])
    if not ok: raise Err(409, "A verified measurement with this test, date and source already exists")
    t.db.execute("UPDATE fields SET name=?,value=?,unit=?,date=?,ref_low=?,ref_high=?,flag=?,status='approved',measurement_id=? WHERE id=?", (f["name"], f["value"], f["unit"], f["date"], f["ref_low"], f["ref_high"], f["flag"], t.last_id, fid))
    t.db.execute("INSERT INTO audit(at,action,detail) VALUES(datetime('now'),'approve_field',?)", (json.dumps({"field": fid, "measurement": t.last_id}),)); t.db.commit()
    return do_update(t, doc["patient"], f"approved lab values from report '{doc['filename']}'") if update else None

# ---------- explainability / organisation helpers ----------
def D(x):
    """Format a date without platform-specific strftime flags (works on Windows and Unix)."""
    ts = pd.Timestamp(x)
    return f"{ts.day} {ts.strftime('%b %Y')}"
def dd(a, b): return (pd.Timestamp(b) - pd.Timestamp(a)).days
def pname(t, pid):
    r = t.db.execute("SELECT name FROM profile WHERE patient=?", (pid,)).fetchone(); return (r and r[0]) or pid
def ref_date(t, pid):
    qs = ["SELECT MAX(date) FROM measurements WHERE patient=? AND verified=1", "SELECT MAX(start) FROM cycles WHERE patient=?", "SELECT MAX(date) FROM symptoms WHERE patient=?",
          "SELECT substr(MAX(ts),1,10) FROM timeseries WHERE patient=?", "SELECT substr(MAX(as_of),1,10) FROM snapshots WHERE patient=?"]
    v = [x for x in (t.db.execute(q, (pid,)).fetchone()[0] for q in qs) if x]; return max(v) if v else None
def cycle_summary(t, pid):
    c = rows(t, "SELECT start,end FROM cycles WHERE patient=? ORDER BY start", (pid,)); L = [dd(c[i]["start"], c[i+1]["start"]) for i in range(len(c)-1)]
    o = {"patient": pid, "n_starts": len(c), "lengths": L, "method": "Cycle length = days from one recorded period start to the next recorded start. A length is calculated only between two consecutive recorded starts; unrecorded periods are never inferred or filled in."}
    if L:
        med = statistics.median(L); o.update(median=med, min=min(L), max=max(L), over35=sum(x > 35 for x in L), under21=sum(x < 21 for x in L))
        if len(L) >= 3: o["estimate"] = {"date": (pd.Timestamp(c[-1]["start"]) + pd.Timedelta(days=round(med))).strftime("%Y-%m-%d"), "basis": f"last recorded start + median recorded interval ({med:g} days)", "note": "Estimate only - not an observed event. Cycles in PCOS are often irregular, so the real date may differ widely."}
    return o
def ts_coverage(t, pid):
    out = []
    for m, n, a, b, fl in t.db.execute("SELECT metric,COUNT(*),MIN(ts),MAX(ts),SUM(quality!='ok') FROM timeseries WHERE patient=? GROUP BY metric", (pid,)).fetchall():
        ts = pd.to_datetime([r[0] for r in t.db.execute("SELECT ts FROM timeseries WHERE patient=? AND metric=? ORDER BY ts", (pid, m))], utc=True)
        d = ts.to_series().diff().dt.total_seconds().dropna()/60; step = float(d.median()) if len(d) else None; gaps = []
        if step:
            for i, v in d[d > max(15, 3*step)].items(): gaps.append({"from": (i - pd.Timedelta(minutes=float(v))).strftime("%Y-%m-%dT%H:%M:%SZ"), "to": i.strftime("%Y-%m-%dT%H:%M:%SZ"), "minutes": round(float(v))})
        exp = int((ts[-1] - ts[0]).total_seconds()/60/step) + 1 if step else n
        out.append({"metric": m, "unit": UNIT.get(m), "n": n, "first": a, "last": b, "flagged": fl or 0, "step_min": step, "gap_count": len(gaps), "gaps": gaps[:6], "coverage_pct": round(100*n/exp, 1) if exp else None,
                    "batches": [r[0] for r in t.db.execute("SELECT DISTINCT batch FROM timeseries WHERE patient=? AND metric=?", (pid, m))]})
    return out
def journey(t, pid):
    need_patient(t, pid); S = []
    cs = cycle_summary(t, pid); c = rows(t, "SELECT start FROM cycles WHERE patient=? ORDER BY start", (pid,)); it = []
    if len(c) >= 2:
        it.append({"text": f"{len(c)} period starts are recorded between {D(c[0]['start'])} and {D(c[-1]['start'])}. The time between consecutive recorded starts ranged from {cs['min']} to {cs['max']} days (median {cs['median']:g}).", "kind": "observation", "dates": [c[0]["start"], c[-1]["start"]]})
        if cs["over35"] or cs["under21"]: it.append({"text": f"{cs['over35']} of {len(cs['lengths'])} recorded intervals were longer than 35 days and {cs['under21']} shorter than 21 days. The 2023 PCOS guideline uses 21-35 days as its reference range for a regular cycle in adults. This is a count, not a diagnosis.", "kind": "reference", "dates": []})
    elif len(c) == 1: it.append({"text": f"One period start is recorded ({D(c[0]['start'])}). At least two are needed to calculate a cycle length.", "kind": "observation", "dates": [c[0]["start"]]})
    S.append({"title": "Menstrual cycle", "tab": "cycle", "empty": "No periods have been recorded yet.", "items": it})
    by = {}
    for r in rows(t, "SELECT * FROM symptoms WHERE patient=? ORDER BY date", (pid,)): by.setdefault(r["kind"], {}).setdefault(r["date"][:7], []).append(r["severity"])
    it = []
    for k, m in by.items():
        ms = sorted(m); nm = k.replace("_", " ").capitalize(); mon = lambda x: pd.Timestamp(x + "-01").strftime("%b %Y")
        if len(ms) >= 2: it.append({"text": f"{nm}: average {statistics.mean(m[ms[0]]):.1f} in {mon(ms[0])} and {statistics.mean(m[ms[-1]]):.1f} in {mon(ms[-1])} (0-4 scale, patient-reported; {len(ms)} months with entries).", "kind": "observation", "dates": []})
        else: it.append({"text": f"{nm}: entries exist for one month only ({mon(ms[0])}), so no change over time can be described.", "kind": "observation", "dates": []})
    S.append({"title": "Symptoms (patient-reported)", "tab": "symptoms", "empty": "No symptom entries have been recorded.", "items": it})
    by = {}
    for m in rows(t, "SELECT * FROM measurements WHERE patient=? AND verified=1 ORDER BY date", (pid,)): by.setdefault(m["test"].lower(), []).append(m)
    it = []
    for k, v in by.items():
        a, b = v[0], v[-1]; units = {x["unit"] for x in v}; rng = f" Laboratory reference range: {b['ref_low']:g}-{b['ref_high']:g} {b['unit']}." if b["ref_low"] is not None and b["ref_high"] is not None else ""
        out = ""
        if rng and b["ref_high"] is not None and b["value"] > b["ref_high"]: out = " The latest value is above the laboratory's range (a lab range, not a diagnostic threshold)."
        elif rng and b["ref_low"] is not None and b["value"] < b["ref_low"]: out = " The latest value is below the laboratory's range (a lab range, not a diagnostic threshold)."
        if len(units) > 1: it.append({"text": f"{a['test']}: recorded in different units ({', '.join(sorted(units))}), so values are not compared.", "kind": "observation", "dates": [a["date"], b["date"]]})
        elif len(v) >= 2: it.append({"text": f"{a['test']}: {a['value']:g} {a['unit']} on {D(a['date'])} and {b['value']:g} {b['unit']} on {D(b['date'])}.{rng}{out}", "kind": "observation", "dates": [a["date"], b["date"]]})
        else: it.append({"text": f"{a['test']}: one result, {a['value']:g} {a['unit']} on {D(a['date'])}.{rng}{out}", "kind": "observation", "dates": [a["date"]]})
    S.append({"title": "Laboratory and clinical measurements", "tab": "tests", "empty": "No verified measurements yet. Upload a lab report or add a measurement.", "items": it})
    MN = {"glucose": "Glucose", "hr": "Heart rate", "steps": "Steps", "hrv_ms": "Heart-rate variability", "sleep_min": "Sleep duration"}
    it = [{"text": f"{MN.get(x['metric'], x['metric'])}: {x['n']:,} readings between {D(x['first'])} and {D(x['last'])} ({x['coverage_pct']}% of the expected readings; {x['gap_count']} gap(s) longer than {max(15, round(3*x['step_min']))} minutes). Imported from file, not a live device.", "kind": "observation", "dates": [x["first"][:10], x["last"][:10]]} for x in ts_coverage(t, pid)]
    S.append({"title": "Wearable data", "tab": "physio", "empty": "No physiological data has been imported.", "items": it})
    sn = rows(t, "SELECT version,as_of,reason FROM snapshots WHERE patient=? ORDER BY version DESC LIMIT 1", (pid,)); pend = rows(t, "SELECT d.filename,COUNT(*) n FROM fields f JOIN documents d ON d.id=f.document_id WHERE d.patient=? AND f.status='pending' GROUP BY d.id", (pid,))
    it = []
    if sn: it.append({"text": f"The Digital Twin is at version {sn[0]['version']}, built from data up to {D(sn[0]['as_of'])} ({sn[0]['reason']}).", "kind": "observation", "dates": [sn[0]["as_of"][:10]]})
    for x in pend: it.append({"text": f"{x['n']} value(s) read from '{x['filename']}' are awaiting your review.", "kind": "observation", "dates": []})
    r = readiness(t, pid)
    if not r["available"]: it.append({"text": "Prediction is unavailable: " + "; ".join(r.get("reasons", [])) + ".", "kind": "observation", "dates": []})
    S.append({"title": "Twin, reviews and missing information", "tab": "twin", "empty": "Nothing recorded yet.", "items": it})
    return {"patient": pid, "name": pname(t, pid), "sections": S, "has_data": any(s["items"] for s in S[:4])}
def quality(t, pid):
    need_patient(t, pid); ref = ref_date(t, pid); age = lambda d: dd(d, ref) if d and ref else None
    ms = rows(t, "SELECT * FROM measurements WHERE patient=? AND verified=1", (pid,)); cats = []
    for c in ["Hormone", "Glycaemic/metabolic", "Lipid", "Cardiovascular", "Thyroid/other endocrine", "Other diagnostic", "Other"]:
        r = [m for m in ms if (m["category"] or "Other") == c]
        if not r and c in ("Other", "Thyroid/other endocrine", "Other diagnostic"): continue
        last = max((m["date"] for m in r), default=None); cats.append({"category": c, "n": len(r), "detail": ", ".join(sorted({m["test"] for m in r})), "latest": last, "age_days": age(last), "stale": bool(last and age(last) > 180), "stale_after": 180, "tab": "tests"})
    for nm, q, lim, tab in [("Menstrual cycle", "SELECT COUNT(*),MAX(start) FROM cycles WHERE patient=?", 120, "cycle"), ("Symptoms", "SELECT COUNT(*),MAX(date) FROM symptoms WHERE patient=?", 60, "symptoms")]:
        n, last = t.db.execute(q, (pid,)).fetchone(); cats.append({"category": nm, "n": n, "detail": "", "latest": last, "age_days": age(last), "stale": bool(last and age(last) > lim), "stale_after": lim, "tab": tab})
    cov = ts_coverage(t, pid); issues = []
    by = {}
    for m in ms: by.setdefault(m["test"].lower(), []).append(m)
    for k, v in by.items():
        us = {m["unit"] for m in v}
        if len(us) > 1: issues.append({"type": "Inconsistent units", "text": f"{v[0]['test']} is recorded in more than one unit ({', '.join(sorted(us))}). Values are not compared.", "tab": "tests"})
        seen = {}
        for m in v: seen.setdefault(m["date"], []).append(m)
        for d, g in seen.items():
            if len(g) > 1: issues.append({"type": "Possible duplicate or conflict", "text": f"{g[0]['test']} has {len(g)} verified results on {D(d)} ({', '.join(str(x['value']) for x in g)} {g[0]['unit']}).", "tab": "tests"})
    for x in cov:
        if x["flagged"]: issues.append({"type": "Flagged readings", "text": f"{x['flagged']} {x['metric']} reading(s) were out of range and are excluded from analysis (kept in storage).", "tab": "physio"})
        if x["gap_count"]: issues.append({"type": "Gaps in wearable data", "text": f"{x['metric']}: {x['gap_count']} gap(s) longer than expected, e.g. {D(x['gaps'][0]['from'])} for {x['gaps'][0]['minutes']} minutes.", "tab": "physio"})
    for c in cats:
        if c["stale"]: issues.append({"type": "Not recent", "text": f"{c['category']}: latest entry is {c['age_days']} days older than the patient's most recent record (flagged after {c['stale_after']} days).", "tab": c["tab"]})
    pend = t.db.execute("SELECT COUNT(*) FROM fields f JOIN documents d ON d.id=f.document_id WHERE d.patient=? AND f.status='pending'", (pid,)).fetchone()[0]
    failed = t.db.execute("SELECT COUNT(*) FROM documents WHERE patient=? AND status='failed'", (pid,)).fetchone()[0]
    if pend: issues.append({"type": "Awaiting review", "text": f"{pend} value(s) read from uploaded reports are not yet verified and are NOT used by the twin.", "tab": "reports"})
    if failed: issues.append({"type": "Extraction failed", "text": f"{failed} uploaded report(s) could not be read; values must be entered manually.", "tab": "reports"})
    prov = {"clinician_verified_from_reports": sum(1 for m in ms if m["document_id"]), "clinician_entered": sum(1 for m in ms if not m["document_id"]), "patient_reported_symptoms": t.db.execute("SELECT COUNT(*) FROM symptoms WHERE patient=?", (pid,)).fetchone()[0],
            "patient_reported_history": t.db.execute("SELECT COUNT(*) FROM history WHERE patient=? AND provenance='patient-reported'", (pid,)).fetchone()[0], "clinician_confirmed_history": t.db.execute("SELECT COUNT(*) FROM history WHERE patient=? AND provenance='clinician-confirmed'", (pid,)).fetchone()[0],
            "imported_samples": sum(x["n"] for x in cov), "awaiting_review": pend}
    sn = rows(t, "SELECT version,as_of,state FROM snapshots WHERE patient=? ORDER BY version DESC LIMIT 1", (pid,)); r = readiness(t, pid)
    return {"patient": pid, "reference_date": ref, "categories": cats, "coverage": cov, "issues": issues, "provenance": prov, "twin": {"version": sn[0]["version"] if sn else None, "as_of": sn[0]["as_of"] if sn else None, "missing": json.loads(sn[0]["state"])["missing"] if sn else ["no snapshot yet"], "can_update": bool(ref)},
            "prediction": r, "concepts": {"completeness": "Whether records exist, how recent they are, and where time-series have gaps. Shown above as counts and dates - it is not a score.", "model_uncertainty": "The prediction model outputs an uncalibrated score and no uncertainty interval. NAARI does not display a confidence percentage because nothing here can justify one.", "clinical_validity": "Not established. The model was trained only on synthetic data and has not been clinically validated."}}
def since(t, pid, frm, to):
    need_patient(t, pid); ref = ref_date(t, pid)
    if not ref: return {"empty": True}
    try: to = to or ref; frm = frm or (pd.Timestamp(ref) - pd.Timedelta(days=90)).strftime("%Y-%m-%d"); assert dd(frm, to) > 0
    except Exception: raise Err(422, "Choose two valid dates (YYYY-MM-DD) with the first earlier than the second")
    span = dd(frm, to); pf = (pd.Timestamp(frm) - pd.Timedelta(days=span)).strftime("%Y-%m-%d"); R = {"from": frm, "to": to, "reference_date": ref, "prev_from": pf}
    ms = rows(t, "SELECT * FROM measurements WHERE patient=? AND verified=1 ORDER BY date", (pid,))
    R["new_measurements"] = [{"label": "New", **{k: m[k] for k in ("test", "value", "unit", "date", "source")}} for m in ms if frm < m["date"] <= to]
    ch = []; by = {}
    for m in ms: by.setdefault(m["test"].lower(), []).append(m)
    for k, v in by.items():
        b = [m for m in v if m["date"] <= frm]; a = [m for m in v if frm < m["date"] <= to]
        if a and b:
            x, y = b[-1], a[-1]
            if x["unit"] != y["unit"]: ch.append({"label": "Insufficient data", "test": y["test"], "note": f"units differ ({x['unit']} vs {y['unit']}); not compared", "before": x, "after": y})
            else: ch.append({"label": "Changed" if x["value"] != y["value"] else "Unchanged", "test": y["test"], "unit": y["unit"], "before": {"value": x["value"], "date": x["date"]}, "after": {"value": y["value"], "date": y["date"]}, "delta": round(y["value"] - x["value"], 3)})
    R["changed_measurements"] = ch
    c = rows(t, "SELECT start,end FROM cycles WHERE patient=? ORDER BY start", (pid,)); R["cycles"] = [{"label": "New", "start": x["start"], "end": x["end"], "interval_from_previous": dd(c[i-1]["start"], x["start"]) if i else None} for i, x in enumerate(c) if frm < x["start"] <= to]
    sy = rows(t, "SELECT * FROM symptoms WHERE patient=?", (pid,)); R["symptoms"] = []
    for k in sorted({s["kind"] for s in sy}):
        cur = [s["severity"] for s in sy if s["kind"] == k and frm < s["date"] <= to]; pr = [s["severity"] for s in sy if s["kind"] == k and pf < s["date"] <= frm]
        if cur and pr: R["symptoms"].append({"label": "Changed" if statistics.mean(cur) != statistics.mean(pr) else "Unchanged", "kind": k, "before_mean": round(statistics.mean(pr), 2), "after_mean": round(statistics.mean(cur), 2), "n_before": len(pr), "n_after": len(cur)})
        elif cur: R["symptoms"].append({"label": "Insufficient data", "kind": k, "note": f"{len(cur)} new entr{'y' if len(cur)==1 else 'ies'}, but none in the earlier comparison window ({D(pf)} to {D(frm)})"})
    R["physiology"] = [{"label": "New", "metric": r[0], "batch": r[1], "n": r[2], "first": r[3], "last": r[4]} for r in t.db.execute("SELECT metric,batch,COUNT(*),MIN(ts),MAX(ts) FROM timeseries WHERE patient=? AND substr(ts,1,10)>? AND substr(ts,1,10)<=? GROUP BY metric,batch", (pid, frm, to))]
    R["twin"] = {"versions_in_window": rows(t, "SELECT version,as_of,reason,(SELECT COUNT(*) FROM json_each(changes)) n_changes FROM snapshots WHERE patient=? AND substr(as_of,1,10)>? AND substr(as_of,1,10)<=? ORDER BY version", (pid, frm, to)), "version_at_start": (rows(t, "SELECT version,as_of FROM snapshots WHERE patient=? AND substr(as_of,1,10)<=? ORDER BY version DESC LIMIT 1", (pid, frm)) or [None])[0]}
    R["awaiting_review"] = [{"label": "Awaiting review", **x} for x in rows(t, "SELECT d.id doc,d.filename,COUNT(*) n FROM fields f JOIN documents d ON d.id=f.document_id WHERE d.patient=? AND f.status='pending' GROUP BY d.id", (pid,))]
    return R
# ---------- views ----------
def summary(t, pid):
    need_patient(t, pid); q = lambda s: t.db.execute(s, (pid,)).fetchone()[0]
    streams = rows(t, "SELECT metric,unit,COUNT(*) n,MIN(ts) first,MAX(ts) last,SUM(quality!='ok') flagged,MAX(batch) batch FROM timeseries WHERE patient=? GROUP BY metric", (pid,))
    snap = rows(t, "SELECT * FROM snapshots WHERE patient=? ORDER BY version DESC LIMIT 1", (pid,))
    if snap: snap = snap[0]; snap["state"] = json.loads(snap["state"]); snap["changes"] = json.loads(snap["changes"])
    prof = rows(t, "SELECT * FROM profile WHERE patient=?", (pid,)); cyc = rows(t, "SELECT start FROM cycles WHERE patient=? ORDER BY start", (pid,))
    ll = (pd.Timestamp(cyc[-1]["start"]) - pd.Timestamp(cyc[-2]["start"])).days if len(cyc) >= 2 else None
    warn = []
    for s in streams:
        if s["flagged"]: warn.append(f"{s['flagged']} {s['metric']} values flagged out-of-range and excluded from analysis")
    if snap: warn += snap["state"]["warnings"]
    return {"patient": pid, "profile": prof[0] if prof else None, "synthetic": True,
            "counts": {**verified_counts(t, pid), "symptom_entries": q("SELECT COUNT(*) FROM symptoms WHERE patient=?"), "cycles": len(cyc), "documents": q("SELECT COUNT(*) FROM documents WHERE patient=?"), "snapshots": q("SELECT COUNT(*) FROM snapshots WHERE patient=?"), "predictions": q("SELECT COUNT(*) FROM predictions WHERE patient=?")},
            "streams": streams, "latest_cycle_length": ll, "snapshot": snap or None, "warnings": warn,
            "pending_reviews": rows(t, "SELECT d.id,d.filename,COUNT(f.id) n FROM documents d JOIN fields f ON f.document_id=d.id AND f.status='pending' WHERE d.patient=? GROUP BY d.id", (pid,)),
            "documents": rows(t, "SELECT id,filename,uploaded_at,status,method FROM documents WHERE patient=? ORDER BY id DESC LIMIT 5", (pid,)),
            "readiness": readiness(t, pid), "history": rows(t, "SELECT * FROM history WHERE patient=? ORDER BY date", (pid,))}
def timeline(t, pid):
    E = []; add = lambda d, c, ti, de, so: E.append({"date": d, "category": c, "title": ti, "detail": de, "source": so})
    for r in rows(t, "SELECT * FROM documents WHERE patient=?", (pid,)): add(r["uploaded_at"][:10], "reports", f"Report uploaded: {r['filename']}", f"status {r['status']}; method {r['method'] or 'n/a'}", "upload")
    for r in rows(t, "SELECT * FROM measurements WHERE patient=? AND verified=1", (pid,)): add(r["date"], "labs", f"{r['test']}: {r['value']:g} {r['unit']}", f"ref {r['ref_low']}-{r['ref_high']}" if r["ref_low"] is not None else "no reference range recorded", r["source"])
    for r in rows(t, "SELECT * FROM cycles WHERE patient=?", (pid,)): add(r["start"], "cycle", "Period start", f"end {r['end']}" if r["end"] else "end not recorded", "recorded")
    for r in rows(t, "SELECT * FROM symptoms WHERE patient=?", (pid,)): add(r["date"], "symptoms", f"{r['kind']}: {r['severity']:g}", r["scale"], "patient-reported")
    for r in rows(t, "SELECT * FROM history WHERE patient=?", (pid,)): add(r["date"], "history", f"{r['kind']}: {r['text']}", r["provenance"], r["provenance"])
    for r in rows(t, "SELECT batch,metric,COUNT(*) n,MIN(ts) a,MAX(ts) b,source FROM timeseries WHERE patient=? GROUP BY batch,metric", (pid,)): add(r["a"][:10], "physiology", f"Imported {r['n']} {r['metric']} samples", f"batch {r['batch']}: {r['a']} to {r['b']}", r["source"])
    for r in rows(t, "SELECT * FROM snapshots WHERE patient=?", (pid,)): add(r["as_of"][:10], "twin", f"Digital Twin v{r['version']}", f"{len(json.loads(r['changes']))} {pl(len(json.loads(r['changes'])), 'value', 'values')} added or changed · recorded reason: {r['reason']}", "twin engine")
    for r in rows(t, "SELECT p.*,o.observed FROM predictions p LEFT JOIN outcomes o ON o.prediction_id=p.id WHERE p.patient=?", (pid,)):
        add(r["made_at"][:10], "predictions", f"Prediction #{r['id']} at {r['made_at']}", f"model {r['model_version']}, horizon {r['horizon_min']} min", "model")
        if r["observed"] is not None: add(r["made_at"][:10], "predictions", f"Outcome recorded for #{r['id']}: {'event occurred' if r['observed'] else 'no event'}", "from stored CGM", "observed")
    return sorted(E, key=lambda e: e["date"], reverse=True)
def do_predict(t, pid, as_of):
    as_of = as_of or readiness(t, pid)["as_of"]
    if not as_of: return {"available": False, "reasons": ["no glucose time-series imported"]}
    r = t.predict(pid, as_of)
    if r["available"]:
        m = json.load(open(f"{ROOT}/models/metrics.json")); names = m["features"]; r["features"] = dict(zip(names, [round(x, 3) for x in r["features"]]))
        hb = t.db.execute("SELECT value,date,source,id FROM measurements WHERE patient=? AND LOWER(test)='hba1c' AND verified=1 ORDER BY date DESC LIMIT 1", (pid,)).fetchone()
        r["evidence"] = {"hba1c": dict(hb) if hb else None}; r["as_of"] = as_of
    return r
def predictions(t, pid):
    P = rows(t, "SELECT p.*,o.observed,o.observed_max FROM predictions p LEFT JOIN outcomes o ON o.prediction_id=p.id WHERE p.patient=? ORDER BY p.id DESC", (pid,))
    for p in P: p["inputs"] = json.loads(p["inputs"])
    return P
def series(t, pid, q):
    m = q.get("metric", ["glucose"])[0]; a = q.get("from", ["0000"])[0]; b = q.get("to", ["9999"])[0]
    r = t.db.execute("SELECT ts,value,quality FROM timeseries WHERE patient=? AND metric=? AND ts>=? AND ts<=? ORDER BY ts", (pid, m, a, b)).fetchall(); step = max(1, len(r)//1500)
    return {"metric": m, "unit": UNIT.get(m), "points": [list(x) for x in r[::step]], "n": len(r), "downsample_step": step}
# ---------- Doctor brief: ONE canonical, display-ready structure rendered by both the browser print view and the server PDF ----------
def DT(x):
    """Format a timestamp in UTC without platform-specific strftime flags."""
    ts = pd.Timestamp(x)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return f"{ts.day} {ts.strftime('%b %Y %H:%M UTC')}"
G = lambda v: f"{v:g}"
def N2(v): return f"{v:,.2f}".rstrip("0").rstrip(".")
def pl(n, a, b): return a if n == 1 else b
VITAL_RE = r"systolic|diastolic|blood pressure|weight|waist|bmi|pulse"
def is_vital(m):   # the project's existing laboratory-vs-vital-sign classification (used by the measurements page and the Doctor Brief)
    return m["category"] == "Cardiovascular" or bool(re.search(VITAL_RE, m["test"], re.I))
def verified_counts(t, pid):
    """Verified measurements for one patient, counted from that patient's own records: total, laboratory results, vital signs."""
    ms = rows(t, "SELECT test,category FROM measurements WHERE patient=? AND verified=1", (pid,)); v = sum(1 for m in ms if is_vital(m))
    return {"verified_measurements": len(ms), "verified_labs": len(ms) - v, "verified_vitals": v}
def slug(s): return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
MNAME = {"glucose": "Glucose (CGM)", "hr": "Heart rate", "steps": "Steps", "hrv_ms": "Heart-rate variability", "sleep_min": "Sleep duration"}
SNAME = {"glucose": "glucose", "hr": "heart rate", "steps": "steps", "hrv_ms": "heart-rate variability", "sleep_min": "sleep"}
FEATN = {"cgm.mean": ("Average glucose over the last 14 days of imported data", "mg/dL"), "cgm.tir_70_180_pct": ("Time in range: share of readings between 70 and 180 mg/dL", "%"), "cgm.tar_gt180_pct": ("Time above range: share of readings above 180 mg/dL", "%"), "cgm.n": ("Number of valid glucose readings used", ""), "cgm.hours_since_last": ("Time since the newest glucose reading, at the version date", "hours")}
KINDL = {"diagnosis": "Diagnosis", "medication": "Medication", "family": "Family history", "history": "History"}
SECLAB = {"history": "medical history", "measurements": "clinical measurements", "cycles": "menstrual cycles", "symptoms": "symptoms", "physiology": "physiological data", "twin": "Digital Twin", "predictions": "predictions", "review": "review items", "quality": "data-quality checks"}
def change_text(c, st):
    if c["feature"].startswith("clinical."):
        k = c["feature"][9:]; nm = f"{k} (latest verified result)"; u = (st["clinical"].get(k) or {}).get("unit", "")
    else: nm, u = FEATN.get(c["feature"], (c["feature"], ""))
    f = lambda v: N2(v) + (f" {u}" if u else "")
    s = f"{nm} is now {f(c['new'])} (not recorded before)" if c["old"] is None else (f"{nm} is no longer available (was {f(c['old'])})" if c["new"] is None else f"{nm} changed from {f(c['old'])} to {f(c['new'])}")
    return s + ". " + (f"Source: {c['source']}." if c.get("source") else "The cause cannot be determined from the available evidence.")
PRED_TEXT = ("The experimental model concerns one future glucose-threshold event: whether sensor glucose goes above 180 mg/dL within the next 120 minutes. It does not diagnose PCOS and does not predict PCOS progression or complications. "
             "It was trained and evaluated only on synthetic data and has not been clinically validated. The value shown is an uncalibrated model score, not a probability or risk, and no confidence interval is produced.")
def brief_data(t, pid, simulate=None):
    need_patient(t, pid); R = {}
    prof = (rows(t, "SELECT * FROM profile WHERE patient=?", (pid,)) or [{}])[0]; today = dt.date.today(); name = prof.get("name") or pid
    B = {"patient": pid, "name": name, "age": prof.get("age"), "generated": D(today.isoformat()), "generated_iso": today.isoformat(), "reference_date": None, "errors": {}}
    def sect(k, fn):
        try:
            if simulate == k: raise RuntimeError("simulated failure (test hook)")
            B[k] = fn()
        except Exception as e: B[k] = None; B["errors"][k] = f"the {SECLAB[k]} section could not be built ({type(e).__name__})"
    rd = ref_date(t, pid); B["reference_date"] = D(rd) if rd else None
    def f_hist():
        H = rows(t, "SELECT * FROM history WHERE patient=? ORDER BY date,id", (pid,)); R["hist"] = H
        return [{"date": D(h["date"]), "type": KINDL.get(h["kind"], h["kind"]), "text": h["text"], "provenance": h["provenance"]} for h in H]
    def f_meas():
        ms = rows(t, "SELECT * FROM measurements WHERE patient=? ORDER BY lower(test),date", (pid,)); R["meas"] = ms; docs = {d["id"]: d for d in rows(t, "SELECT id,uploaded_at FROM documents WHERE patient=?", (pid,))}
        isv = is_vital
        def row(m):
            up = D(docs[m["document_id"]]["uploaded_at"][:10]) if m["document_id"] in docs else None
            ref = f"{G(m['ref_low'])}–{G(m['ref_high'])} {m['unit']}" if m["ref_low"] is not None and m["ref_high"] is not None else "not recorded"
            return {"test": m["test"], "value": G(m["value"]), "unit": m["unit"], "collected": D(m["date"]), "reference": ref, "flag": m["flag"] if m["flag"] else "not provided", "status": "Verified" if m["verified"] else "Unverified — not used", "source": m["source"] + (f" · report uploaded {up}" if up else "")}
        return {"labs": [row(m) for m in ms if not isv(m)], "vitals": [row(m) for m in ms if isv(m)],
                "note": "“Collected” is the date the sample or reading was taken; the report upload date, where shown, is separate. The reference range and the flag are exactly as supplied by the laboratory report: a reference range is the lab’s own range, not a diagnostic threshold, and “not provided” means the source gave no flag. NAARI does not assign flags or label any result abnormal."}
    def f_cyc():
        c = rows(t, "SELECT start,end FROM cycles WHERE patient=? ORDER BY start", (pid,)); R["cyc"] = c; cs = cycle_summary(t, pid); out = []
        for i, r in enumerate(c):
            ln = dd(r["start"], c[i+1]["start"]) if i + 1 < len(c) else None
            out.append({"start": D(r["start"]), "end": D(r["end"]) if r["end"] else "not recorded", "bleeding": str(dd(r["start"], r["end"]) + 1) if r["end"] else "—", "to_next": str(ln) if ln is not None else "—", "note": "interval >90 d: periods may be unrecorded (not assumed)" if (ln or 0) > 90 else ""})
        L = cs["lengths"]; txt = ""
        if c: txt = (f"{len(c)} period {pl(len(c), 'start is', 'starts are')} recorded between {D(c[0]['start'])} and {D(c[-1]['start'])}. Cycle length is calculated as the number of days between one recorded period start and the next recorded start; it is calculated only between two recorded starts, and missing periods are never inferred or filled in."
                     + (f" For {len(L)} {pl(len(L), 'interval', 'intervals')}: range {cs['min']}–{cs['max']} days, median {G(cs['median'])}." if L else " At least two recorded starts are needed to calculate a length."))
        return {"n": len(c), "text": txt, "rows": out, "note": "Estimated future periods are not included in this brief; only recorded events are shown."}
    def f_sym():
        sy = rows(t, "SELECT * FROM symptoms WHERE patient=? ORDER BY date,id", (pid,)); R["sym"] = sy
        if not sy: return {"n": 0}
        months = sorted({s["date"][:7] for s in sy}); shown = months[-8:]; kinds = list(dict.fromkeys(s["kind"] for s in sy)); ml = lambda m: pd.Timestamp(m + "-01").strftime("%b %Y")
        return {"n": len(sy), "text": f"{len(sy)} {pl(len(sy), 'entry', 'entries')} between {D(sy[0]['date'])} and {D(sy[-1]['date'])}. Scale: {sy[0]['scale'] or '0–4'}. Reported by the patient; not a measurement of hormones. Cells show the monthly mean; blank = nothing recorded that month.",
                "months": [ml(m) for m in shown], "rows": [{"symptom": k.replace("_", " "), "entries": str(sum(1 for s in sy if s["kind"] == k)), "cells": [(f"{statistics.mean([s['severity'] for s in sy if s['kind'] == k and s['date'].startswith(m)]):.1f}" if any(s["kind"] == k and s["date"].startswith(m) for s in sy) else "") for m in shown]} for k in kinds],
                "note": f"Showing the latest 8 of {len(months)} months." if len(months) > 8 else ""}
    def f_phys():
        cov = ts_coverage(t, pid); R["cov"] = cov; out = []
        for c in cov:
            gaps = f"{c['gap_count']} longer than {max(15, round(3*c['step_min']))} min: " + ", ".join(f"{D(g['from'])} ({g['minutes']} min)" for g in c["gaps"][:3]) if c["gap_count"] else "none detected"
            out.append({"stream": MNAME.get(c["metric"], c["metric"]), "unit": c["unit"], "readings": f"{c['n']:,}", "period": f"{DT(c['first'])} to {DT(c['last'])}", "coverage": f"{G(c['coverage_pct'])}%", "gaps": gaps, "flagged": str(c["flagged"])})
        return {"streams": out, "note": "*Readings present ÷ readings expected at the observed sampling interval. Data was imported from files (synthetic); it is not from a live device.", "derived": None}
    def f_twin():
        S = rows(t, "SELECT * FROM snapshots WHERE patient=? ORDER BY version DESC", (pid,)); R["twin"] = S
        if not S: return {"built": False}
        sn = S[0]; st = json.loads(sn["state"]); ch = [c for c in json.loads(sn["changes"]) if c["feature"] != "cgm.hours_since_last"]
        return {"built": True, "version": sn["version"], "text": f"Latest version: {sn['version']}, built from data up to {DT(sn['as_of'])} ({sn['reason']}). {len(S)} {pl(len(S), 'version is', 'versions are')} saved. Scope: the twin state currently uses verified clinical measurements and a 14-day glucose summary. Menstrual cycles and symptoms are stored and shown in this brief but are not twin features; heart rate and steps feed only the prediction model.",
                "heading": f"What changed in version {sn['version']}", "changes": [change_text(c, st) for c in ch[:8]], "more": max(0, len(ch) - 8), "empty_changes": "No changes were recorded in this version." if not ch else "",
                "history": [f"Version {v['version']} · data up to {D(v['as_of'])} · {v['reason']} · {len(json.loads(v['changes']))} {pl(len(json.loads(v['changes'])), 'change', 'changes')}" for v in S] if len(S) > 1 else []}
    def f_pred():
        P = rows(t, "SELECT p.*,o.observed,o.observed_max FROM predictions p LEFT JOIN outcomes o ON o.prediction_id=p.id WHERE p.patient=? ORDER BY p.id", (pid,)); R["pred"] = P; un = None
        if not P:
            r = readiness(t, pid); un = "No prediction has been run for this patient." + (" Currently unavailable: " + "; ".join(r["reasons"]) + "." if not r["available"] and r.get("reasons") else "")
        return {"text": PRED_TEXT, "rows": [{"as_of": DT(p["made_at"]), "target": f"Glucose >180 mg/dL within {p['horizon_min']} min", "score": f"{p['prob']:.3f}", "model": p["model_version"], "outcome": "not yet recorded" if p["observed"] is None else ("event occurred" if p["observed"] else "no event") + f" (max {p['observed_max']:.0f} mg/dL)"} for p in P], "none": un}
    for k, fn in (("history", f_hist), ("measurements", f_meas), ("cycles", f_cyc), ("symptoms", f_sym), ("physiology", f_phys), ("twin", f_twin), ("predictions", f_pred)): sect(k, fn)
    if B["physiology"] and R.get("twin"):                                             # derived glucose summary from the latest snapshot
        cg = json.loads(R["twin"][0]["state"]).get("cgm") or {}
        if cg.get("mean") is not None: B["physiology"]["derived"] = f"Derived (latest Twin snapshot, 14 days of glucose up to {D(R['twin'][0]['as_of'])}): mean {G(round(cg['mean'], 1))} mg/dL; {G(round(cg['tir_70_180_pct'], 1))}% of readings between 70 and 180 mg/dL; {G(round(cg['tar_gt180_pct'], 1))}% above 180 mg/dL. Heart-rate and step data are summarised by coverage only."
    def f_pending():                    # GENUINE review items only: values read from uploaded reports whose status is 'pending'
        out = []
        for x in rows(t, "SELECT d.id,d.filename,COUNT(*) n FROM fields f JOIN documents d ON d.id=f.document_id WHERE d.patient=? AND f.status='pending' GROUP BY d.id ORDER BY d.id", (pid,)):
            nm = [r["name"] for r in rows(t, "SELECT name FROM fields WHERE document_id=? AND status='pending' ORDER BY id", (x["id"],))]
            out.append(f"{x['n']} {pl(x['n'], 'value', 'values')} read from “{x['filename']}” {pl(x['n'], 'is', 'are')} awaiting your review and not used: " + ", ".join(nm[:8]) + (f", and {len(nm)-8} more" if len(nm) > 8 else "") + ".")
        return out
    def f_quality():
        q = quality(t, pid)
        return {"issues": [i for i in q["issues"] if i["type"] != "Awaiting review"], "categories": [c["category"] for c in q["categories"] if not c["n"]], "no_wearable": not q["coverage"], "pred": None if q["prediction"]["available"] else (q["prediction"].get("reasons") or [])}
    sect("review", f_pending); sect("quality", f_quality); QQ = B.pop("quality"); RV = B.pop("review"); tw = R.get("twin")
    RI = {"awaiting_review": list(RV or []), "not_recorded": [], "unavailable": [], "data_quality": []}
    if QQ:
        RI["data_quality"] = [f"{i['type']}: {i['text']}" for i in QQ["issues"]]; RI["not_recorded"] += [f"Nothing recorded for “{c}”." for c in QQ["categories"]]
        if QQ["no_wearable"]: RI["not_recorded"].append("No wearable data imported.")
        if QQ["pred"] is not None: RI["unavailable"].append("Prediction unavailable: " + "; ".join(QQ["pred"]) + ".")
    if "twin" not in B["errors"]:
        if not tw: RI["not_recorded"].append("Digital Twin not built.")
        else:
            miss = json.loads(tw[0]["state"])["missing"]
            if miss: RI["not_recorded"].append("Not yet in the Digital Twin: " + ", ".join(miss) + ".")
    RI["unavailable"] = [f"Could not be retrieved: {m}." for m in B["errors"].values()] + RI["unavailable"]
    TTL = {"awaiting_review": "Awaiting your review — values read from uploaded reports that you have not yet approved (not used anywhere)", "not_recorded": "Not recorded — no such records exist for this patient", "unavailable": "Unavailable — could not be retrieved or could not be computed", "data_quality": "Data-quality notes about records that do exist"}
    B["review_items"] = [{"key": k, "title": TTL[k], "items": RI[k]} for k in ("awaiting_review", "not_recorded", "unavailable", "data_quality")]
    # overview (built only from what was retrieved; failures are stated, never counted as zero)
    un = "could not be retrieved"; H, ms, C, SY, cov, P = R.get("hist"), R.get("meas"), R.get("cyc"), R.get("sym"), R.get("cov"), R.get("pred")
    conf = sum(1 for h in H if h["provenance"] == "clinician-confirmed") if H is not None else 0; V = [m for m in (ms or []) if m["verified"]]; T = len({m["test"].lower() for m in V})
    st = [SNAME.get(c["metric"], c["metric"]) for c in cov] if cov is not None else []
    if not B["errors"] and not (H or V or C or SY or st or tw or P): ov = f"No clinical records are stored for {name} ({pid}) yet. Every section below shows what is not recorded."; dx = None
    else:
        parts = [f"{len(H)} history {pl(len(H), 'entry', 'entries')} ({conf} clinician-confirmed, {len(H)-conf} patient-reported)" if H is not None else f"history {un}",
                 f"{len(V)} verified {pl(len(V), 'measurement', 'measurements')} across {T} {pl(T, 'test', 'tests')}" if ms is not None else f"measurements {un}",
                 f"{len(C)} recorded period {pl(len(C), 'start', 'starts')}" if C is not None else f"cycle records {un}",
                 f"{len(SY)} patient-reported symptom {pl(len(SY), 'entry', 'entries')}" if SY is not None else f"symptom entries {un}",
                 ("imported wearable data (" + (", ".join(st[:-1]) + " and " + st[-1] if len(st) > 1 else st[0]) + ")") if st else ("no wearable data" if cov is not None else f"wearable data {un}"),
                 (f"Digital Twin version {tw[0]['version']}" if tw else "Digital Twin not built") if "twin" not in B["errors"] else f"Digital Twin {un}",
                 f"{len(P)} stored {pl(len(P), 'prediction', 'predictions')}" if P is not None else f"predictions {un}"]
        ov = f"Stored records for {name}: " + "; ".join(parts) + "."
        d_ = [h for h in (H or []) if h["kind"] == "diagnosis"]; dx = ("Recorded diagnosis: " + "; ".join(f"{h['text']} ({h['provenance']}, {D(h['date'])})" for h in d_) + ". No diagnosis is inferred by NAARI.") if d_ else None
    B["overview"] = {"text": ov, "diagnosis": dx}
    B["notices"] = {"synthetic": f"Synthetic demonstration data. {name} is a fictional patient; seeded records are synthetic, uploaded or entered values are shown as provided. This is a research-prototype summary, not a diagnostic or treatment document.",
                    "footer": f"NAARI is a research prototype and not a diagnostic or treatment system; it does not replace clinical judgement. Synthetic demonstration data — {name} ({pid}) is fictional. Generated {B['generated']}."}
    return B
def brief_pdf(B):
    from reportlab.lib.pagesizes import A4; from reportlab.lib import colors; from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape as X
    PL = colors.HexColor("#594052"); base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.6, leading=11.5, textColor=colors.HexColor("#302B31"))
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=PL, spaceBefore=10, spaceAfter=3, keepWithNext=1); h3 = ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=9, spaceBefore=5, keepWithNext=1)
    cell = ParagraphStyle("c", parent=base, fontSize=7.4, leading=9.2); hdr = ParagraphStyle("hc", parent=cell, fontName="Helvetica-Bold", textColor=PL); meta = ParagraphStyle("m", parent=base, fontSize=7.6, textColor=colors.HexColor("#77717A"))
    none = ParagraphStyle("n", parent=base, fontName="Helvetica-Oblique", textColor=colors.HexColor("#77717A")); warn = ParagraphStyle("w", parent=base, backColor=colors.HexColor("#FBF5EA"), borderPadding=4, leftIndent=4)
    el = []; P = lambda x, s=base: el.append(Paragraph(X(str(x)), s)); W = 523
    def tab(head, rws, widths):
        data = [[Paragraph(X(h), hdr) for h in head]] + [[Paragraph(X(str(c)), cell) for c in r] for r in rws]; tot = sum(widths); cw = [w * W / tot for w in widths]
        el.append(Table(data, colWidths=cw, repeatRows=1, style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E2F0")), ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#E3DCDF")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))); el.append(Spacer(1, 4))
    E = B["errors"]; sec = lambda n, ti: P(f"{n} · {ti}", h2)
    def err(k):
        if k in E: P(f"Could not be retrieved: {E[k]}. This is not the same as “no records” — the information may exist but is not shown here.", warn); return True
    P("NAARI — Doctor Brief", ParagraphStyle("t", parent=base, fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=PL)); P("PCOS Digital Twin for Clinicians · research prototype", meta); el.append(Spacer(1, 6))
    el.append(Table([[Paragraph("Patient", hdr), Paragraph(X(B["name"] + " (fictional)"), cell), Paragraph("Patient ID", hdr), Paragraph(X(B["patient"]), cell)], [Paragraph("Age", hdr), Paragraph(X(str(B["age"]) if B["age"] is not None else "not recorded"), cell), Paragraph("Report generated", hdr), Paragraph(X(B["generated"]), cell)]]
                    + ([[Paragraph("Records up to", hdr), Paragraph(X(B["reference_date"] + " (most recent dated record on file)"), cell), "", ""]] if B["reference_date"] else []), colWidths=[70, 190, 90, 173], style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#E3DCDF")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("SPAN", (1, 2), (3, 2))] if B["reference_date"] else [("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#E3DCDF"))])))
    el.append(Spacer(1, 6)); el.append(Table([[Paragraph(X(B["notices"]["synthetic"]), cell)]], colWidths=[W], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6E4E9")), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)])))
    sec(1, "Patient overview"); P(B["overview"]["text"]); B["overview"]["diagnosis"] and P(B["overview"]["diagnosis"])
    sec(2, "Medical history")
    if not err("history"): tab(["Date", "Type", "Entry", "Source of information"], [[h["date"], h["type"], h["text"], h["provenance"]] for h in B["history"]], [60, 70, 280, 113]) if B["history"] else P("No history entries are recorded.", none)
    sec(3, "Menstrual-cycle history")
    if not err("cycles"):
        c = B["cycles"]
        if not c["n"]: P("No periods are recorded.", none)
        else: P(c["text"]); tab(["Period start", "Period end", "Bleeding days (derived)", "Days to next recorded start (derived)", "Note"], [[r["start"], r["end"], r["bleeding"], r["to_next"], r["note"]] for r in c["rows"]], [75, 75, 80, 110, 183]); P(c["note"], meta)
    sec(4, "Symptoms (patient-reported)")
    if not err("symptoms"):
        s = B["symptoms"]
        if not s["n"]: P("No symptom entries are recorded.", none)
        else: P(s["text"]); tab(["Symptom", "Entries"] + s["months"], [[r["symptom"], r["entries"]] + r["cells"] for r in s["rows"]], [95, 45] + [50] * len(s["months"])); s["note"] and P(s["note"], meta)
    sec(5, "Clinical measurements")
    if not err("measurements"):
        m = B["measurements"]
        if not (m["labs"] or m["vitals"]): P("No laboratory tests or vital-sign measurements are recorded.", none)
        else:
            for ti, L in (("Laboratory tests", m["labs"]), ("Vital signs (including blood pressure)", m["vitals"])):
                P(ti, h3)
                if L: tab(["Test", "Value", "Unit", "Collected", "Laboratory reference range", "Lab-reported flag", "Status", "Source"], [[r["test"], r["value"], r["unit"], r["collected"], r["reference"], r["flag"], r["status"], r["source"]] for r in L], [64, 34, 34, 50, 66, 60, 46, 169])
                else: P("None recorded.", none)
            P(m["note"], meta)
    sec(6, "Physiological data (imported files)")
    if not err("physiology"):
        p = B["physiology"]
        if not p["streams"]: P("No wearable data has been imported for this patient.", none)
        else: tab(["Stream", "Unit", "Readings", "Period covered", "Coverage*", "Gaps", "Out-of-range (excluded)"], [[r["stream"], r["unit"], r["readings"], r["period"], r["coverage"], r["gaps"], r["flagged"]] for r in p["streams"]], [58, 52, 52, 126, 44, 113, 58]); P(p["note"], meta); p["derived"] and P(p["derived"])
    sec(7, "Digital Twin")
    if not err("twin"):
        w = B["twin"]
        if not w["built"]: P("No Digital Twin version has been built.", none)
        else:
            P(w["text"]); P(w["heading"], h3)
            for x in w["changes"]: P("• " + x)
            w["empty_changes"] and P(w["empty_changes"]); w["more"] and P(f"…and {w['more']} more change(s).", meta)
            if w["history"]: P("Version history", h3); [P("• " + x) for x in w["history"]]
    sec(8, "Experimental prediction")
    if not err("predictions"):
        q = B["predictions"]; P(q["text"])
        if q["rows"]: tab(["As of", "Target", "Model score", "Model version", "Observed outcome"], [[r["as_of"], r["target"], r["score"], r["model"], r["outcome"]] for r in q["rows"]], [95, 140, 60, 90, 138])
        else: P(q["none"], none)
    sec(9, "Review items, missing information and unavailable data"); shown = False
    for g in B["review_items"]:
        if g["items"]: shown = True; P(g["title"], h3); [P("• " + x) for x in g["items"]]
    if not shown: P("Nothing awaiting review, missing or unavailable was detected by the automatic checks.", none)
    def deco(c, d):
        c.saveState(); c.setStrokeColor(colors.HexColor("#E3DCDF")); c.line(36, 44, 559, 44); c.setFont("Helvetica", 6.6); c.setFillColor(colors.HexColor("#77717A")); f = B["notices"]["footer"]; cut = f.rfind(" ", 0, 150)
        c.drawString(36, 35, f[:cut]); c.drawString(36, 27, f[cut+1:]); c.drawRightString(559, 27, f"Page {d.page}"); c.restoreState()
    buf = io.BytesIO(); SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=34, bottomMargin=56, title=f"NAARI Doctor Brief — {B['name']} ({B['patient']}) — {B['generated_iso']}", author="NAARI research prototype (synthetic data)").build(el, onFirstPage=deco, onLaterPages=deco); return buf.getvalue()
# ---------- routing ----------
def body_json(h):
    n = int(h.headers.get("Content-Length") or 0)
    if n > MAXB + 1024: raise Err(413, "Request too large")
    return json.loads(h.rfile.read(n) or b"{}") if n else {}
def raw_body(h):
    n = int(h.headers.get("Content-Length") or 0)
    if n > MAXB + 1024: raise Err(413, "File exceeds 10 MB limit")
    return h.rfile.read(n)
class H(BaseHTTPRequestHandler):
    server_version = "NAARI"
    def log_message(self, *a): pass     # never log request contents
    def send(self, code, ctype, data, extra=()):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'self'")
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)
    def js(self, code, obj, extra=()): self.send(code, "application/json", json.dumps(obj, default=str).encode(), extra)
    def user(self):
        c = self.headers.get("Cookie", ""); m = re.search(r"naari_session=([A-Za-z0-9_-]+)", c); return SESSIONS.get(m.group(1)) if m else None
    def handle_req(self, method):
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query)
        try:
            if method == "GET" and p == "/": return self.send(200, "text/html; charset=utf-8", open(f"{ROOT}/web/index.html", "rb").read())
            if method == "GET" and p == "/ui2.js": return self.send(200, "application/javascript; charset=utf-8", open(f"{ROOT}/web/ui2.js", "rb").read())
            if p == "/api/health": return self.js(200, {"ok": True})
            if p == "/api/demo-login" and method == "POST":
                if os.environ.get("NAARI_DEMO", "1") == "0": raise Err(403, "Demo login disabled")
                return self.login("clinician")
            if p == "/api/login" and method == "POST":
                b = body_json(self); t = T(); r = t.db.execute("SELECT salt,pw_hash FROM users WHERE username=?", (b.get("username"),)).fetchone()
                if not r or not hmac.compare_digest(records.hash_pw(b.get("password", ""), r[0])[1], r[1]): raise Err(401, "Invalid credentials")
                return self.login(b["username"])
            if p == "/api/me": return self.js(200, {"user": self.user()})
            if p == "/api/logout": return self.js(200, {"ok": True}, [("Set-Cookie", "naari_session=; Max-Age=0; Path=/")])
            user = self.user()
            if not p.startswith("/api/"): raise Err(404, "Not found")
            if not user: raise Err(401, "Sign in required")
            t = T(); res = self.route(t, method, p, q)
            if isinstance(res, tuple): return self.send(200, res[0], res[1], res[2] if len(res) > 2 else ())
            return self.js(200, res)
        except Err as e: self.js(e.code, {"error": e.msg})
        except json.JSONDecodeError: self.js(400, {"error": "Invalid JSON"})
        except Exception as e: self.js(500, {"error": f"Server error: {type(e).__name__}"})
    def login(self, name):
        tok = secrets.token_urlsafe(24); SESSIONS[tok] = name
        self.js(200, {"user": name, "role": "clinician"}, [("Set-Cookie", f"naari_session={tok}; HttpOnly; SameSite=Strict; Path=/")])
    def route(self, t, m, p, q):
        g = lambda rx: re.fullmatch(rx, p)
        if p == "/api/me": return {"user": self.user()}
        if p == "/api/patients":
            out = rows(t, "SELECT p.id,p.synthetic,pr.age,pr.summary,pr.name,(SELECT MAX(version) FROM snapshots WHERE patient=p.id) twin FROM patients p LEFT JOIN profile pr ON pr.patient=p.id ORDER BY p.id")
            for r in out: r.update(verified_counts(t, r["id"])); r["labs"] = r["verified_measurements"]   # "labs" kept only for older API consumers: it has always counted ALL verified measurements (lab results + vital signs)
            return out
        sm = g(r"/api/samples/([\w.]+)")
        if sm and sm[1] in ("synthetic_lab_report.pdf", "synthetic_lab_report_scan.png", "demo_new_cgm_batch.csv"):
            return ({"pdf": "application/pdf", "png": "image/png", "csv": "text/csv"}[sm[1].rsplit(".", 1)[1]], open(f"{ROOT}/data/samples/{sm[1]}", "rb").read())
        if p == "/api/model": return json.load(open(f"{ROOT}/models/metrics.json"))
        if p == "/api/reset" and m == "POST":
            if os.environ.get("NAARI_DEMO", "1") == "0": raise Err(403, "Reset disabled")
            seedmod.reset(DB); return {"ok": True}
        x = g(r"/api/patients/([\w-]+)/([\w.]+)(?:/(\w+))?")
        if x:
            pid, what, sub = x.groups(); need_patient(t, pid)
            if what == "summary": return summary(t, pid)
            if what == "timeline": return timeline(t, pid)
            if what == "series": return series(t, pid, q)
            if what == "brief": return brief_data(t, pid, q.get("simulate_failure", [None])[0] if os.environ.get("NAARI_TEST_HOOKS") == "1" else None)   # test hook disabled unless env set
            if what == "report.pdf":
                B = brief_data(t, pid); return ("application/pdf", brief_pdf(B), [("Content-Disposition", f'attachment; filename="NAARI_Doctor_Brief_{pid}_{slug(B["name"])}_{B["generated_iso"]}.pdf"')])
            if what == "measurements":
                if m == "GET": return rows(t, "SELECT * FROM measurements WHERE patient=? ORDER BY date,test", (pid,))
                b = body_json(self)
                try: v = float(b["value"]); dt.date.fromisoformat(b["date"]); name = b["test"].strip(); unit = b["unit"].strip(); assert name and unit
                except Exception: raise Err(422, "test, numeric value, unit and ISO date are required")
                num = lambda k: float(b[k]) if b.get(k) not in (None, "") else None
                if not t.add_measurement(pid, name, v, unit, b["date"], "manual entry (clinician)", True, category=b.get("category") or extract.category(name), ref_low=num("ref_low"), ref_high=num("ref_high"), notes=b.get("notes")): raise Err(409, "Duplicate measurement")
                return {"id": t.last_id, "twin": do_update(t, pid, f"manual measurement '{name}'")}
            if what == "journey": return journey(t, pid)
            if what == "quality": return quality(t, pid)
            if what == "since": return since(t, pid, q.get("from", [None])[0], q.get("to", [None])[0])
            if what == "cycles" and sub == "summary": return cycle_summary(t, pid)
            if what == "cycles":
                if m == "GET":
                    c = rows(t, "SELECT * FROM cycles WHERE patient=? ORDER BY start", (pid,))
                    for i, r in enumerate(c):
                        r["length_to_next"] = (pd.Timestamp(c[i+1]["start"]) - pd.Timestamp(r["start"])).days if i+1 < len(c) else None
                        r["note"] = "interval >90 d: periods may be unrecorded (not assumed)" if (r["length_to_next"] or 0) > 90 else None
                        r["bleeding_days"] = (dd(r["start"], r["end"]) + 1) if r["end"] else None
                    return c
                b = body_json(self)
                try: dt.date.fromisoformat(b["start"]); assert not b.get("end") or dt.date.fromisoformat(b["end"]) >= dt.date.fromisoformat(b["start"])
                except Exception: raise Err(422, "Valid start date required; end must not precede start")
                try: t.db.execute("INSERT INTO cycles(patient,start,end,notes) VALUES(?,?,?,?)", (pid, b["start"], b.get("end") or None, b.get("notes"))); t.db.commit()
                except sqlite3.IntegrityError: raise Err(409, "A period starting that day is already recorded")
                return {"ok": True}
            if what == "symptoms":
                if m == "GET": return rows(t, "SELECT * FROM symptoms WHERE patient=? ORDER BY date", (pid,))
                b = body_json(self)
                try: dt.date.fromisoformat(b["date"]); sv = float(b["severity"]); assert 0 <= sv <= 4 and b["kind"].strip()
                except Exception: raise Err(422, "date, symptom and severity 0-4 required")
                t.db.execute("INSERT INTO symptoms(patient,date,kind,severity,scale,notes) VALUES(?,?,?,?,?,?)", (pid, b["date"], b["kind"].strip().lower(), sv, "0-4 (0 none, 4 severe)", b.get("notes"))); t.db.commit(); return {"ok": True}
            if what == "documents":
                if m == "GET": return rows(t, "SELECT id,filename,uploaded_at,status,method,(SELECT COUNT(*) FROM fields WHERE document_id=documents.id AND status='pending') pending,(SELECT COUNT(*) FROM fields WHERE document_id=documents.id AND status='approved') approved FROM documents WHERE patient=? ORDER BY id DESC", (pid,))
                return upload(t, pid, q.get("filename", [""])[0], raw_body(self))
            if what == "timeseries" and sub == "preview": return preview_series(t, pid, raw_body(self))
            if what == "timeseries":
                if m != "POST": raise Err(405, "Importing data requires POST")
                raw = raw_body(self); pv = preview_series(t, pid, raw); out, mets, bad, issues = parse_series(raw)
                batch = q.get("batch", [""])[0] or q.get("filename", ["upload"])[0] + "-" + dt.datetime.now().strftime("%H%M%S")
                s = t.import_timeseries(pid, out, batch, "file import: " + q.get("filename", ["?"])[0]); s["preview"] = pv; return s
            if what == "twin" and sub == "update":
                if m != "POST": raise Err(405, "Updating the Digital Twin requires POST")
                b = body_json(self); why = re.sub(r"[^\w .,'()-]", "", str(b.get("reason") or ""))[:90].strip()
                return do_update(t, pid, why or "manual update from interface")
            if what == "twin" and sub == "compare":
                S = {r["version"]: r for r in rows(t, "SELECT * FROM snapshots WHERE patient=?", (pid,))}
                a, b = int(q["a"][0]), int(q["b"][0])
                if a not in S or b not in S: raise Err(404, "Unknown snapshot version")
                fa, fb = flat(json.loads(S[a]["state"])), flat(json.loads(S[b]["state"]))
                return {"a": a, "b": b, "diff": [{"feature": k, "a": fa.get(k), "b": fb.get(k)} for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)], "unchanged_count": sum(1 for k in fa if k in fb and fa[k] == fb[k])}
            if what == "twin":
                S = rows(t, "SELECT * FROM snapshots WHERE patient=? ORDER BY version DESC", (pid,))
                for s in S: s["state"] = json.loads(s["state"]); s["changes"] = json.loads(s["changes"])
                return S
            if what == "predict" and m == "POST":
                b = body_json(self); return do_predict(t, pid, b.get("as_of"))
            if what == "predictions": return predictions(t, pid)
        x = g(r"/api/documents/(\d+)(?:/(file))?")
        if x:
            d = get_doc(t, int(x[1]))
            if x[2]:
                ct = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "csv": "text/plain", "json": "text/plain"}[d["kind"]]
                return (ct, open(os.path.join(UP, rows(t, "SELECT stored FROM documents WHERE id=?", (d["id"],))[0]["stored"]), "rb").read(), [("Content-Disposition", "inline")])
            return d
        x = g(r"/api/documents/(\d+)/(\w+)")
        x2 = g(r"/api/fields/(\d+)")
        if x2 and m == "POST":
            b = body_json(self); fid = int(x2[1])
            if b.get("action") == "reject":
                f = t.db.execute("SELECT status FROM fields WHERE id=?", (fid,)).fetchone()
                if not f: raise Err(404, "Unknown field")
                if f[0] == "approved": raise Err(409, "An approved value cannot be rejected; it is already a verified measurement")
                t.db.execute("UPDATE fields SET status='rejected' WHERE id=?", (fid,)); t.db.commit(); return {"ok": True, "status": "rejected"}
            if b.get("action") == "approve": return {"twin": approve_field(t, fid, b.get("edits", {}), b.get("update", True))}
            raise Err(400, "action must be approve or reject")
        x3 = g(r"/api/documents/(\d+)/fields")
        if x3 and m == "POST":
            b = body_json(self); get_doc(t, int(x3[1]))
            if not str(b.get("name") or "").strip() or not str(b.get("value") or "").strip(): raise Err(422, "test name and result are required")
            t.db.execute("INSERT INTO fields(document_id,page,name,value,unit,date,method) VALUES(?,?,?,?,?,?,'manual-entry')", (int(x3[1]), 1, b.get("name"), b.get("value"), b.get("unit"), b.get("date"))); t.db.commit(); return get_doc(t, int(x3[1]))
        x4 = g(r"/api/predictions/(\d+)/outcome")
        if x4 and m == "POST": return t.record_outcome(int(x4[1]))
        raise Err(404, "Not found")
    def do_GET(self): self.handle_req("GET")
    def do_POST(self): self.handle_req("POST")
def main():
    if not os.path.exists(DB): seedmod.reset(DB)
    port = int(os.environ.get("PORT", "8000")); print(f"NAARI running at http://127.0.0.1:{port}  (local research prototype, synthetic demo data)")
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), H).serve_forever()
if __name__ == "__main__": main()
