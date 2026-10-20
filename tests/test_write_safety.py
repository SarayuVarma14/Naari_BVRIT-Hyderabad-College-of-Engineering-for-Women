"""Fix 5: server-side safeguards on the existing write endpoints (no new auth system). API-only; starts a throwaway server on a temp DB.
Run: python3 tests/test_write_safety.py"""
import os, sys, json, subprocess, time, tempfile, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
port = 20000 + os.getpid() % 20000; tmp = tempfile.mkdtemp(); B = f"http://127.0.0.1:{port}"; cookie = {}; FAILS = []
srv = subprocess.Popen([sys.executable, "-m", "naari.server"], cwd=ROOT, env={**os.environ, "NAARI_DB": f"{tmp}/t.db", "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: FAILS.append(m)
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
    call("/api/demo-login", "POST"); P = "/api/patients/SYN-000"
    nsnap = lambda: len(call(P + "/twin")[1]); cnt = lambda pid="SYN-000": call(f"/api/patients/{pid}/summary")[1]["counts"]["verified_measurements"]
    # 1. a plain GET (link, prefetch, <img>) must never change data
    v = nsnap(); s, e = call(P + "/twin/update", "GET"); check(s == 405 and nsnap() == v, f"GET /twin/update is refused (HTTP {s}) and creates no Twin version")
    s, e = call(P + "/timeseries", "GET"); check(s == 405, f"GET /timeseries (import) is refused (HTTP {s})")
    # 2. unauthenticated and unknown-patient writes
    for path, d in (("/twin/update", {}), ("/measurements", {"test": "X", "value": 1, "unit": "u", "date": "2026-01-01"}), ("/cycles", {"start": "2026-01-01"}), ("/symptoms", {"date": "2026-01-01", "kind": "acne", "severity": 1}), ("/predict", {})):
        s, _ = call(P + path, "POST", d, auth=False); check(s == 401, f"unauthenticated POST {path} -> {s} (401 expected)")
        s, _ = call("/api/patients/SYN-999" + path, "POST", d); check(s == 404, f"POST {path} for an unknown patient -> {s} (404 expected)")
    # 3. validation: a failed write returns an error and changes nothing
    before = cnt()
    for d, why in (({"test": "Ferritin", "value": "abc", "unit": "ng/mL", "date": "2026-03-01"}, "non-numeric value"), ({"test": "", "value": 5, "unit": "ng/mL", "date": "2026-03-01"}, "empty test name"), ({"test": "Ferritin", "value": 5, "unit": "ng/mL", "date": "not-a-date"}, "bad date")):
        s, e = call(P + "/measurements", "POST", d); check(s == 422 and cnt() == before, f"measurement with {why} -> {s}; verified count unchanged ({before})")
    s, e = call(P + "/cycles", "POST", {"start": "2026-03-10", "end": "2026-03-01"}); check(s == 422, f"period ending before it starts -> {s}")
    s, e = call(P + "/symptoms", "POST", {"date": "2026-03-01", "kind": "acne", "severity": 9}); check(s == 422, f"symptom severity 9 (scale 0-4) -> {s}")
    # 4. a successful write is persisted and every count agrees afterwards; a duplicate is refused without a second row
    s, r = call(P + "/measurements", "POST", {"test": "Ferritin", "value": 55, "unit": "ng/mL", "date": "2026-03-01"})
    check(s == 200 and "version" in r["twin"] and cnt() == before + 1, f"valid measurement saved -> verified count {before} -> {cnt()}")
    check(next(p for p in call("/api/patients")[1] if p["id"] == "SYN-000")["verified_measurements"] == before + 1, "patient list count equals the summary count after the write")
    s, e = call(P + "/measurements", "POST", {"test": "Ferritin", "value": 55, "unit": "ng/mL", "date": "2026-03-01"}); check(s == 409 and cnt() == before + 1, f"duplicate measurement -> {s}, count unchanged")
    # 5. report fields: approve / reject rules
    pdf = open(f"{ROOT}/data/samples/synthetic_lab_report.pdf", "rb").read(); s, d = call(P + "/documents?filename=lab.pdf", "POST", raw=pdf); fid = [f for f in d["fields"] if f["name"] == "HbA1c"][0]["id"]; other = [f["id"] for f in d["fields"] if f["id"] != fid]
    check(cnt() == before + 1, "uploaded values are pending: the verified count did not change on upload")
    s, e = call("/api/fields/99999", "POST", {"action": "reject"}); check(s == 404, f"rejecting an unknown field -> {s} (was a silent 'ok')")
    s, e = call("/api/fields/99999", "POST", {"action": "approve"}); check(s == 404, f"approving an unknown field -> {s}")
    s, e = call(f"/api/fields/{fid}", "POST", {"action": "approve", "edits": {"name": "HbA1c"}}); check(s == 200 and cnt() == before + 2, "approving a pending value adds exactly one verified measurement")
    s, e = call(f"/api/fields/{fid}", "POST", {"action": "reject"}); check(s == 409 and cnt() == before + 2, f"an already-approved value cannot be 'rejected' -> {s} (it stays verified)")
    s, e = call(f"/api/fields/{other[0]}", "POST", {"action": "reject"}); check(s == 200 and e.get("status") == "rejected" and cnt() == before + 2, "rejecting a pending value does not change the verified count")
    s, e = call(f"/api/fields/{fid}", "POST", {"action": "explode"}); check(s == 400, f"unknown action -> {s}")
    # 6. manual field on a report
    s, e = call("/api/documents/99999/fields", "POST", {"name": "X", "value": "1"}); check(s == 404, f"adding a field to an unknown document -> {s}")
    s, e = call(f"/api/documents/{d['id']}/fields", "POST", {"name": "", "value": ""}); check(s == 422, f"adding an empty field -> {s}")
    s, e = call(f"/api/documents/{d['id']}/fields", "POST", {"name": "Ferritin2", "value": "3", "unit": "ng/mL", "date": "2026-03-02"}); check(s == 200 and cnt() == before + 2, "a manually added report field is pending (not verified) until approved")
    # 7. uploads: type/size/content checks still enforced
    for fn, body, want in (("x.exe", b"MZ", 415), ("x.pdf", b"not a pdf", 415), ("x.pdf", b"", 400)):
        s, e = call(P + f"/documents?filename={fn}", "POST", raw=body); check(s == want, f"upload {fn} ({len(body)} bytes) -> {s} (expected {want})")
    # 8. Kavya (empty record) gains nothing from any failed or refused write
    k0 = call("/api/patients/SYN-002/summary")[1]["counts"]; call("/api/patients/SYN-002/measurements", "POST", {"test": "X", "value": "zz", "unit": "u", "date": "2026-01-01"}); call("/api/patients/SYN-002/twin/update", "GET")
    check(call("/api/patients/SYN-002/summary")[1]["counts"] == k0 and call("/api/patients/SYN-002/twin")[1] == [], "Kavya: refused writes leave her record empty and create no Twin")
finally: srv.terminate()
print("\nFAILED:" if FAILS else "\nALL WRITE-SAFETY CHECKS PASSED", *FAILS, sep="\n"); sys.exit(1 if FAILS else 0)
