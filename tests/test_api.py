"""API tests. Starts a throwaway server on a temp DB. Run: python3 tests/test_api.py"""
import os, sys, json, subprocess, time, tempfile, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
port = 8791; tmp = tempfile.mkdtemp(); env = {**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
B = f"http://127.0.0.1:{port}"; cookie = {}
def call(path, method="GET", data=None, raw=None, auth=True):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else (b"{}" if method == "POST" else None))
    r = urllib.request.Request(B + path, data=body, method=method, headers={"Cookie": cookie.get("c", "")} if auth else {})
    try:
        with urllib.request.urlopen(r) as x:
            if x.headers.get("Set-Cookie"): cookie["c"] = x.headers["Set-Cookie"].split(";")[0]
            b = x.read(); return x.status, (json.loads(b) if "json" in x.headers.get("Content-Type", "") else b)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
try:
    for _ in range(60):
        try: urllib.request.urlopen(B + "/api/health"); break
        except Exception: time.sleep(.5)
    s, _ = call("/api/patients", auth=False); assert s == 401                                  # unauthorized blocked
    s, _ = call("/api/demo-login", "POST"); assert s == 200
    s, pts = call("/api/patients"); assert s == 200 and len(pts) == 3
    s, sm = call("/api/patients/SYN-000/summary"); assert sm["counts"]["verified_measurements"] == 9 and not sm["readiness"]["available"]
    assert any("HbA1c" in r for r in sm["readiness"]["reasons"]), sm["readiness"]
    v0 = sm["snapshot"]["version"]
    s, e = call("/api/patients/SYN-000/documents?filename=x.exe", "POST", raw=b"MZ"); assert s == 415
    s, e = call("/api/patients/SYN-000/documents?filename=x.pdf", "POST", raw=b"not a pdf"); assert s == 415
    s, e = call("/api/patients/SYN-000/documents?filename=../../etc/passwd.png", "POST", raw=b"\x89PNG junk"); assert s in (200, 415)
    pdf = open(f"{ROOT}/data/samples/synthetic_lab_report.pdf", "rb").read()
    s, d = call("/api/patients/SYN-000/documents?filename=lab.pdf", "POST", raw=pdf); assert s == 200 and len(d["fields"]) == 8 and d["method"] == "pdf-text-layer"
    f0 = [f for f in d["fields"] if f["name"] == "HbA1c"][0]
    s, e = call(f"/api/fields/{f0['id']}", "POST", {"action": "approve", "edits": {"date": ""}}); assert s == 422                    # never guesses missing date
    s, r = call(f"/api/fields/{f0['id']}", "POST", {"action": "approve", "edits": {"name": "HbA1c"}}); assert s == 200 and r["twin"]["version"] == v0 + 1
    assert any(c["feature"] == "clinical.HbA1c" for c in r["twin"]["changes"]) and r["twin"]["readiness"]["available"]
    s, e = call(f"/api/fields/{f0['id']}", "POST", {"action": "approve"}); assert s == 409
    call(f"/api/fields/{d['fields'][1]['id']}", "POST", {"action": "reject"})
    s, u = call("/api/patients/SYN-000/twin/update", "POST"); assert u["unchanged"] and u["changes"] == [] and u["version"] == v0 + 1     # no fabricated change
    s, p = call("/api/patients/SYN-000/predict", "POST", {}); assert s == 200 and p["available"] and 0 <= p["prob"] <= 1
    s, o = call(f"/api/predictions/{p['id']}/outcome", "POST"); assert o["recorded"] is False                                         # horizon not observed yet
    csv = open(f"{ROOT}/data/samples/demo_new_cgm_batch.csv", "rb").read()
    s, pv = call("/api/patients/SYN-000/timeseries/preview", "POST", raw=csv); assert s == 200 and pv["metrics"]["glucose"]["n"] > 1000
    s, im = call("/api/patients/SYN-000/timeseries?filename=new.csv", "POST", raw=csv); assert im["inserted"] > 3000
    s, im2 = call("/api/patients/SYN-000/timeseries?filename=new.csv", "POST", raw=csv); assert im2["inserted"] == 0 and im2["duplicates"] > 0
    s, u = call("/api/patients/SYN-000/twin/update", "POST"); assert u["version"] == v0 + 2 and any(c["feature"].startswith("cgm.") for c in u["changes"])
    s, o = call(f"/api/predictions/{p['id']}/outcome", "POST"); assert o["recorded"] is True                                          # now observed from stored CGM
    s, cmp_ = call(f"/api/patients/SYN-000/twin/compare?a={v0}&b={v0+2}"); assert len(cmp_["diff"]) > 0
    s, bad = call("/api/patients/SYN-000/timeseries/preview", "POST", raw=b"timestamp,glucose\nnot-a-date,100\n2026-04-01T00:00:00Z,9999\n"); assert bad["invalid_timestamps"] == 1 and bad["metrics"]["glucose"]["out_of_range"] == 1
    s, c = call("/api/patients/SYN-000/cycles", "POST", {"start": "2026-04-01", "end": "2026-03-01"}); assert s == 422
    s, c = call("/api/patients/SYN-000/cycles"); assert c[1]["length_to_next"] == (__import__("datetime").date(2025, 7, 30) - __import__("datetime").date(2025, 6, 14)).days
    s, tl = call("/api/patients/SYN-000/timeline"); assert {"reports", "labs", "cycle", "symptoms", "physiology", "twin", "predictions"} <= {e["category"] for e in tl}
    s, pdf = call("/api/patients/SYN-000/report.pdf"); assert pdf[:4] == b"%PDF" and len(pdf) > 2000
    import pdfplumber, io; txt = pdfplumber.open(io.BytesIO(pdf)).pages[0].extract_text() + "".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(pdf)).pages[1:]); assert "hba1c" in txt.lower() and "SYN-000" in txt
    s, e = call("/api/patients/SYN-002/predict", "POST", {}); assert e["available"] is False and e["reasons"]
    s, _ = call("/api/documents/1/file"); assert s == 200
    # ---- organisation / explainability layer ----
    names = {p["id"]: p["name"] for p in pts}; assert names == {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}
    s, j = call("/api/patients/SYN-000/journey"); assert j["has_data"] and all("text" in i and i["kind"] in ("observation", "reference") for sec in j["sections"] for i in sec["items"])
    txt = " ".join(i["text"] for sec in j["sections"] for i in sec["items"]); assert "8 period starts" in txt and "Total Testosterone" in txt and "diagnosed" not in txt.lower() and "you have" not in txt.lower()
    s, j2 = call("/api/patients/SYN-002/journey"); assert not j2["has_data"] and all(not sec["items"] for sec in j2["sections"][:4])            # empty patient -> honest empty
    s, j1 = call("/api/patients/SYN-001/journey"); t1 = " ".join(i["text"] for sec in j1["sections"] for i in sec["items"]); assert "48" in t1 and "Acne" not in t1 and "61" not in t1   # no leakage between patients
    s, tl1 = call("/api/patients/SYN-001/timeline"); assert all("acne" not in e["title"] for e in tl1)
    s, sm2 = call("/api/patients/SYN-002/summary"); assert sm2["counts"]["verified_measurements"] == 0 and sm2["history"] == [] and sm2["snapshot"] is None
    s, cs = call("/api/patients/SYN-000/cycles/summary"); assert cs["median"] == 42 and cs["estimate"]["date"] == "2026-04-07" and "never inferred" in cs["method"]
    s, cs2 = call("/api/patients/SYN-001/cycles/summary"); assert cs2["n_starts"] == 0 and "estimate" not in cs2
    s, qu = call("/api/patients/SYN-000/quality"); assert {"categories", "coverage", "issues", "provenance", "concepts"} <= set(qu) and qu["coverage"][0]["gap_count"] == 0 and qu["provenance"]["clinician_verified_from_reports"] >= 1
    s, qu2 = call("/api/patients/SYN-002/quality"); assert qu2["prediction"]["available"] is False and qu2["twin"]["can_update"] is False
    s, sn = call("/api/patients/SYN-000/since"); assert sn["new_measurements"] is not None and sn["from"] < sn["to"] and any(c["label"] in ("Changed", "Unchanged") for c in sn["changed_measurements"])
    assert all(c["label"] != "Insufficient data" or "units differ" in c["note"] for c in sn["changed_measurements"]) and not any(("better" in json.dumps(sn).lower() or "worse" in json.dumps(sn).lower()) for _ in [0])
    s, bad = call("/api/patients/SYN-000/since?from=2026-03-01&to=2026-01-01"); assert s == 422
    s, e = call("/api/patients/SYN-002/since"); assert e.get("empty") is True
    s, tw = call("/api/patients/SYN-000/twin"); assert all("cause" not in json.dumps(c) for c in tw[0]["changes"]) and any(c["source"] and "HbA1c result dated 2026-03-08" in c["source"] for t_ in tw for c in t_["changes"])
    # ---- server-generated PDF is patient-specific (all three patients) ----
    import pdfplumber, io as _io
    def pdf_text(pid):
        s_, b_ = call(f"/api/patients/{pid}/report.pdf"); assert s_ == 200 and b_[:4] == b"%PDF", pid
        return "\n".join((pg.extract_text() or "") for pg in pdfplumber.open(_io.BytesIO(b_)).pages)
    ta, tm, tk = pdf_text("SYN-000"), pdf_text("SYN-001"), pdf_text("SYN-002")
    assert "Ananya Rao" in ta and "SYN-000" in ta and "Metformin" in ta
    assert "Meera Sharma" in tm and "SYN-001" in tm and all(x not in tm for x in ("Ananya", "Metformin", "Rotterdam", "SYN-000", "acne", "Total Cholesterol"))
    assert "Kavya Reddy" in tk and "SYN-002" in tk and all(x not in tk for x in ("Ananya", "Metformin", "Rotterdam", "SYN-000", "Testosterone", "Total Cholesterol"))
    s, _ = call("/api/patients/SYN-001/measurements", "POST", {"test": "HbA1c", "value": 5.9, "unit": "%", "date": "2026-03-01"}); assert s == 200
    assert "HbA1c" in pdf_text("SYN-001") and "HbA1c" not in pdf_text("SYN-002")                                   # regenerated PDF reflects the stored change, only for that patient
    s, q = call("/api/patients/SYN-001/quality"); assert q["patient"] == "SYN-001"; s, c_ = call("/api/patients/SYN-000/cycles/summary"); assert c_["patient"] == "SYN-000"
    s, r = call("/api/reset", "POST"); s, sm = call("/api/patients/SYN-000/summary"); assert sm["snapshot"]["version"] == 1 and sm["counts"]["documents"] == 0
    print("ALL API TESTS PASSED")
finally: srv.terminate()
