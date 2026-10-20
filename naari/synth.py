"""Deterministic SYNTHETIC CGM/HR/steps generator. Not real patient data."""
import numpy as np, pandas as pd

def generate(n_patients=40, days=14, seed=7, start="2026-03-01"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days*288, freq="5min", tz="UTC")
    mins = np.arange(len(idx))*5.0
    rows, meta = [], []
    for p in range(n_patients):
        ir = rng.uniform(0, 1)                       # latent insulin-resistance level
        hba1c = round(5.0 + 1.6*ir + rng.normal(0, .15), 1)
        g = np.full(len(idx), 82 + 18*ir)
        for d in range(days):
            for h in (8, 13, 19):
                t0 = d*1440 + h*60 + rng.normal(0, 25)
                carbs = rng.uniform(.6, 1.4)
                dt = np.clip(mins - t0, 0, None)
                tau = 45
                g += (dt > 0) * carbs * (35 + 110*ir) * (dt/tau) * np.exp(1 - dt/tau)
        noise = np.zeros(len(idx)); e = rng.normal(0, 2.5, len(idx))
        for i in range(1, len(idx)): noise[i] = .8*noise[i-1] + e[i]
        g = g + noise
        hod = (mins % 1440)/60
        steps = rng.poisson(np.where((hod > 7) & (hod < 21), 60, 3))
        hr = 62 + 0.05*steps + 4*np.sin(hod/24*2*np.pi) + rng.normal(0, 2, len(idx))
        rows.append(pd.DataFrame({"patient": f"SYN-{p:03d}", "ts": idx, "glucose": g.round(1),
                                  "hr": hr.round(0), "steps": steps}))
        meta.append({"patient": f"SYN-{p:03d}", "hba1c": hba1c})
    return pd.concat(rows, ignore_index=True), pd.DataFrame(meta)
