"""SQLite-backed Digital Twin: ingest -> validate -> fuse -> versioned snapshot -> gated prediction."""
import json, sqlite3, joblib, numpy as np, pandas as pd
from .model import features_from_window, W, H, THRESH

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, synthetic INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS measurements(id INTEGER PRIMARY KEY, patient TEXT REFERENCES patients(id), test TEXT, value REAL, unit TEXT,
  date TEXT, source TEXT, verified INT DEFAULT 0, category TEXT, ref_low REAL, ref_high REAL, flag TEXT,
  document_id INT, page INT, notes TEXT, orig_value REAL, orig_unit TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(patient,test,date,source));
CREATE TABLE IF NOT EXISTS timeseries(id INTEGER PRIMARY KEY, patient TEXT, metric TEXT, ts TEXT, value REAL, unit TEXT,
  source TEXT, batch TEXT, quality TEXT DEFAULT 'ok', UNIQUE(patient,metric,ts));
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, patient TEXT, version INT, as_of TEXT, state TEXT, changes TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY, patient TEXT, snapshot_version INT, made_at TEXT, horizon_min INT,
  prob REAL, inputs TEXT, model_version TEXT, input_window TEXT);
CREATE TABLE IF NOT EXISTS outcomes(prediction_id INTEGER PRIMARY KEY, observed INT, observed_max REAL, source TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT, action TEXT, detail TEXT);
"""
RANGE = {"glucose": (20, 600), "hr": (25, 220), "steps": (0, 1000), "hrv_ms": (5, 300), "sleep_min": (0, 1440)}
UNIT = {"glucose": "mg/dL", "hr": "bpm", "steps": "count/5min", "hrv_ms": "ms", "sleep_min": "min"}
iso = lambda t: pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

class Twin:
    def __init__(self, path="naari.db", model_path="models/glucose_tar_2h.joblib"):
        self.db = sqlite3.connect(path); self.db.row_factory = None; self.db.executescript(SCHEMA); self.model_path = model_path

    def add_patient(self, pid, synthetic=True):
        self.db.execute("INSERT OR IGNORE INTO patients VALUES(?,?)", (pid, int(synthetic))); self.db.commit()

    def add_measurement(self, pid, test, value, unit, date, source, verified=False, **x):
        c = self.db.execute("INSERT OR IGNORE INTO measurements(patient,test,value,unit,date,source,verified,category,ref_low,ref_high,flag,document_id,page,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pid, test, value, unit, date, source, int(verified), x.get("category"), x.get("ref_low"), x.get("ref_high"), x.get("flag"),
                             x.get("document_id"), x.get("page"), x.get("notes"))); self.db.commit()
        self.last_id = c.lastrowid; return c.rowcount == 1

    def import_timeseries(self, pid, df, batch, source):
        """df columns: ts (tz-aware), glucose/hr/steps. Returns validation summary; nothing silently dropped."""
        s = {"batch": batch, "inserted": 0, "duplicates": 0, "flagged_out_of_range": 0, "missing": 0}
        for m, (lo, hi) in RANGE.items():
            if m not in df: continue
            for ts, v in zip(df.ts, df[m]):
                if pd.isna(v): s["missing"] += 1; continue
                q = "ok" if lo <= v <= hi else "out_of_range"; s["flagged_out_of_range"] += q != "ok"
                c = self.db.execute("INSERT OR IGNORE INTO timeseries(patient,metric,ts,value,unit,source,batch,quality) VALUES(?,?,?,?,?,?,?,?)",
                    (pid, m, iso(ts), float(v), UNIT[m], source, batch, q))
                s["inserted" if c.rowcount else "duplicates"] += 1
        self.db.execute("INSERT INTO audit(at,action,detail) VALUES(datetime('now'),'import',?)", (json.dumps(s),)); self.db.commit(); return s

    def _series(self, pid, metric, lo, hi):
        r = self.db.execute("SELECT ts,value FROM timeseries WHERE patient=? AND metric=? AND quality='ok' AND ts>? AND ts<=? ORDER BY ts",
                            (pid, metric, lo, hi)).fetchall()
        return pd.Series([v for _, v in r], index=pd.DatetimeIndex([t for t, _ in r]) if r else None, dtype=float)

    def update(self, pid, as_of, reason="manual"):
        """Recompute state from verified measurements + eligible time-series up to as_of. Stores a new version + diff."""
        as_of = iso(as_of); state = {"as_of": as_of, "clinical": {}, "cgm": {}, "missing": [], "warnings": [], "sources": {}}
        for t, v, u, d, i in self.db.execute("SELECT test,value,unit,date,id FROM measurements WHERE patient=? AND verified=1 AND date<=? ORDER BY date", (pid, as_of[:10])):
            state["clinical"][t] = {"value": v, "unit": u, "date": d}; state["sources"][t] = i   # latest verified wins
        lo = iso(pd.Timestamp(as_of) - pd.Timedelta(days=14)); g = self._series(pid, "glucose", lo, as_of)
        if len(g):
            hrs = round((pd.Timestamp(as_of) - pd.Timestamp(g.index[-1])).total_seconds()/3600, 1)
            state["cgm"] = {"n": len(g), "mean": round(g.mean(), 1), "tir_70_180_pct": round(100*((g >= 70) & (g <= 180)).mean(), 1),
                            "tar_gt180_pct": round(100*(g > 180).mean(), 1), "hours_since_last": hrs}
            if hrs > 6: state["warnings"].append("CGM stale (>6h)")
        else: state["missing"].append("glucose time-series")
        if not any(k.lower() == "hba1c" for k in state["clinical"]): state["missing"].append("hba1c")
        prev = self.db.execute("SELECT version,state FROM snapshots WHERE patient=? ORDER BY version DESC LIMIT 1", (pid,)).fetchone()
        flat = lambda s: {**{f"clinical.{k}": v["value"] for k, v in s["clinical"].items()}, **{f"cgm.{k}": v for k, v in s["cgm"].items()}}
        old = flat(json.loads(prev[1])) if prev else {}; new = flat(state)
        bl = self.db.execute("SELECT batch FROM timeseries WHERE patient=? AND metric='glucose' AND ts<=? ORDER BY ts DESC LIMIT 1", (pid, as_of)).fetchone()
        def src(k):
            if k.startswith("clinical.") and k[9:] in state["sources"]:
                r = self.db.execute("SELECT source,date FROM measurements WHERE id=?", (state["sources"][k[9:]],)).fetchone(); return f"{k[9:]} result dated {r[1]} ({r[0]})"
            return f"glucose data batch '{bl[0]}'" if bl else None
        changes = [{"feature": k, "old": old.get(k), "new": new.get(k), "source": src(k)} for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
        self.unchanged = bool(prev and not changes)
        if self.unchanged: return prev[0], []
        ver = (prev[0] + 1) if prev else 1
        self.db.execute("INSERT INTO snapshots(patient,version,as_of,state,changes,reason) VALUES(?,?,?,?,?,?)",
                        (pid, ver, as_of, json.dumps(state), json.dumps(changes), reason)); self.db.commit(); return ver, changes

    def predict(self, pid, as_of, dry=False):
        """Gated inference: refuses (with exact reasons) unless inputs satisfy the model's requirements."""
        as_of = iso(as_of); lo = iso(pd.Timestamp(as_of) - pd.Timedelta(minutes=5*W))
        g, hr, st = (self._series(pid, m, lo, as_of) for m in ("glucose", "hr", "steps"))
        hb = self.db.execute("SELECT value FROM measurements WHERE patient=? AND LOWER(test)='hba1c' AND verified=1 AND date<=? ORDER BY date DESC LIMIT 1", (pid, as_of[:10])).fetchone()
        why, need = [], int(W*.9)
        if len(g) < need: why.append(f"needs >= {need} valid glucose samples in the 2h before as_of; found {len(g)}")
        if len(hr) < need: why.append(f"needs >= {need} heart-rate samples in the same window; found {len(hr)}")
        if len(st) < need: why.append(f"needs >= {need} step samples in the same window; found {len(st)}")
        if hb is None: why.append("no verified HbA1c recorded")
        if len(g) and g.iloc[-1] > THRESH: why.append(f"glucose already > {THRESH:.0f} mg/dL; model only predicts new excursions")
        try: model = joblib.load(self.model_path)
        except Exception: why.append("trained model file missing")
        if why: return {"available": False, "reasons": why}
        if dry: return {"available": True, "dry": True}
        n = min(len(g), len(hr), len(st))
        ver = self.db.execute("SELECT MAX(version) FROM snapshots WHERE patient=?", (pid,)).fetchone()[0]
        hour = pd.Timestamp(g.index[-1]).hour + pd.Timestamp(g.index[-1]).minute/60
        x = features_from_window(g.values[-n:], hr.values[-n:], st.values[-n:], hour, hb[0])
        p = float(model.predict_proba(np.array([x]))[0, 1])
        cur = self.db.execute("INSERT INTO predictions(patient,snapshot_version,made_at,horizon_min,prob,inputs,model_version,input_window) VALUES(?,?,?,?,?,?,?,?)",
                              (pid, ver, as_of, 5*H, p, json.dumps(x), "gb-0.1-synthetic", f"{lo}..{as_of}")); self.db.commit()
        return {"available": True, "id": cur.lastrowid, "prob": p, "horizon_min": 5*H, "features": x}

    def record_outcome(self, pred_id, source="cgm_timeseries"):
        """Outcome only from real stored data covering the horizon; otherwise refuses."""
        pid, made, hz = self.db.execute("SELECT patient,made_at,horizon_min FROM predictions WHERE id=?", (pred_id,)).fetchone()
        g = self._series(pid, "glucose", made, iso(pd.Timestamp(made) + pd.Timedelta(minutes=hz)))
        if len(g) < int(H*.9): return {"recorded": False, "reason": f"only {len(g)}/{H} CGM samples in the horizon"}
        obs = int(g.max() > THRESH)
        self.db.execute("INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?)", (pred_id, obs, float(g.max()), source)); self.db.commit()
        return {"recorded": True, "observed": obs, "max": float(g.max())}
