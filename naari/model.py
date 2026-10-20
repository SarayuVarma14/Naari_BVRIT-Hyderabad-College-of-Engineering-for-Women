"""Task: among moments where glucose <=180, predict whether CGM glucose exceeds 180 mg/dL
(consensus 'time above range, level 1') at any point in the next 120 min.
Inputs: previous 2h CGM/HR/steps + latest HbA1c. Split is grouped by patient."""
import json, joblib, numpy as np, pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, precision_score, recall_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
W, H, THRESH = 24, 24, 180.0           # 24 samples x 5 min = 2h history / 2h horizon
FEATURES = ["g_last","g_mean","g_std","g_min","g_max","g_delta15","g_slope","hr_mean","steps_sum","hour_sin","hour_cos","hba1c"]

def features_from_window(g, hr, steps, hour, hba1c):
    g = np.asarray(g, float)
    return [g[-1], g.mean(), g.std(), g.min(), g.max(), g[-1]-g[-4], np.polyfit(range(len(g)), g, 1)[0],
            float(np.mean(hr)), float(np.sum(steps)), np.sin(hour/24*2*np.pi), np.cos(hour/24*2*np.pi), hba1c]

def build(df, meta, stride=6):
    hb = dict(zip(meta.patient, meta.hba1c)); X, y, grp = [], [], []
    for p, d in df.groupby("patient"):
        g, hr, st = d.glucose.values, d.hr.values, d.steps.values
        hours = pd.DatetimeIndex(d.ts).hour.values + pd.DatetimeIndex(d.ts).minute.values/60
        for i in range(W, len(d)-H, stride):
            if g[i-1] > THRESH: continue            # predict new excursions only
            X.append(features_from_window(g[i-W:i], hr[i-W:i], st[i-W:i], hours[i-1], hb[p]))
            y.append(int(g[i:i+H].max() > THRESH)); grp.append(p)
    return pd.DataFrame(X, columns=FEATURES), np.array(y), np.array(grp)

def metrics(y, prob, thr=.5):
    pr = (prob >= thr).astype(int)
    return {"auroc": round(roc_auc_score(y, prob),4), "auprc": round(average_precision_score(y, prob),4),
            "recall": round(recall_score(y, pr),4), "precision": round(precision_score(y, pr, zero_division=0),4),
            "brier": round(brier_score_loss(y, prob),4), "prevalence": round(float(y.mean()),4), "n": int(len(y))}

def train(df, meta, out_model="models/glucose_tar_2h.joblib", out_metrics="models/metrics.json", seed=0):
    X, y, grp = build(df, meta)
    pats = np.array(sorted(set(grp))); rng = np.random.default_rng(seed); rng.shuffle(pats)
    test_p = set(pats[:int(len(pats)*.25)]); te = np.array([p in test_p for p in grp])
    base = make_pipeline(StandardScaler(), LogisticRegression()).fit(X.loc[~te, ["g_last"]], y[~te])
    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=seed).fit(X[~te], y[~te])
    res = {"task": f"P(CGM glucose > {THRESH:.0f} mg/dL within next 120 min | glucose <= {THRESH:.0f} now)",
           "data": "SYNTHETIC (naari/synth.py, seed 7). Not clinical validation.",
           "split": f"grouped by patient: {len(pats)-len(test_p)} train / {len(test_p)} test patients",
           "baseline_logreg_last_glucose": metrics(y[te], base.predict_proba(X.loc[te, ["g_last"]])[:,1]),
           "gradient_boosting": metrics(y[te], model.predict_proba(X[te])[:,1]),
           "features": FEATURES, "model_version": "gb-0.1-synthetic"}
    joblib.dump(model, out_model); json.dump(res, open(out_metrics, "w"), indent=2)
    return res
