import os, json, tempfile, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from naari.synth import generate
from naari.model import train
from naari.twin import Twin
df, meta = generate(); df.to_csv("data/synthetic_cgm.csv", index=False); meta.to_csv("data/synthetic_hba1c.csv", index=False)
res = train(df, meta); print(json.dumps({k: res[k] for k in ("split","baseline_logreg_last_glucose","gradient_boosting")}, indent=1))
db = os.path.join(tempfile.mkdtemp(), "t.db"); t = Twin(db); pid = "SYN-000"; t.add_patient(pid)
d = df[df.patient == pid].reset_index(drop=True); cut = 288*10
t.import_timeseries(pid, d.iloc[:cut], "b1", "synthetic"); as_of = d.ts[cut-1]
v1, _ = t.update(pid, as_of); r = t.predict(pid, as_of)
assert not r["available"] and any("HbA1c" in x for x in r["reasons"]), r        # gate works
assert t.add_measurement(pid, "hba1c", 6.1, "%", "2026-02-20", "lab.pdf", True)
v2, ch = t.update(pid, as_of); assert v2 == 2 and any(c["feature"] == "clinical.hba1c" for c in ch)
dup = t.import_timeseries(pid, d.iloc[:cut], "b1-again", "synthetic"); assert dup["inserted"] == 0 and dup["duplicates"] > 0
bad = d.iloc[cut:cut+3].copy(); bad.loc[bad.index[0], "glucose"] = 5000
s = t.import_timeseries(pid, bad, "b-bad", "synthetic"); assert s["flagged_out_of_range"] == 1
for k in range(cut-1, 288*3, -1):
    r = t.predict(pid, d.ts[k])
    if r["available"]: break
assert r["available"]; print("prediction", round(r["prob"], 3), "at", d.ts[k])
print("outcome before data:", t.record_outcome(r["id"]))
t.import_timeseries(pid, d.iloc[cut:], "b2", "synthetic")
print("outcome after data:", t.record_outcome(r["id"]))
assert Twin(db).db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
print("ALL CORE TESTS PASSED")
