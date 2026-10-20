"""Create the SYNTHETIC demo database, sample report files and sample datasets. `python3 -m naari.seed --reset`"""
import os, sys, shutil, pandas as pd
from .twin import Twin
from . import records
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUT = 288*10
def make_sample_files():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from PIL import Image, ImageDraw, ImageFont
    rows = ["Total Testosterone 52 ng/dL 15 - 70", "HbA1c 6.1 % 4.0 - 5.6 High", "Fasting Glucose 104 mg/dL 70 - 99 High", "Total Cholesterol 201 mg/dL 0 - 200 High",
            "LDL Cholesterol 128 mg/dL 0 - 100 High", "HDL Cholesterol 46 mg/dL 40 - 100", "Triglycerides 168 mg/dL 0 - 150 High", "DHEAS 310 ug/dL 65 - 380"]
    head = ["SYNTHETIC DEMO LAB - fictional report, not a real patient", "Patient: SYN-000", "Collected: 2026-03-08", "Test Result Unit Reference Range Flag"]
    os.makedirs(f"{ROOT}/data/samples", exist_ok=True)
    c = canvas.Canvas(f"{ROOT}/data/samples/synthetic_lab_report.pdf", pagesize=A4); y = 800; c.setFont("Helvetica", 12)
    for ln in head + rows: c.drawString(60, y, ln); y -= 24
    c.save()
    img = Image.new("RGB", (1100, 520), "white"); d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except Exception: f = ImageFont.load_default()
    for i, ln in enumerate(head[:3] + rows[:5]): d.text((40, 30 + i*55), ln, fill="black", font=f)
    img.save(f"{ROOT}/data/samples/synthetic_lab_report_scan.png")
    df = pd.read_csv(f"{ROOT}/data/synthetic_cgm.csv"); d0 = df[df.patient == "SYN-000"].reset_index(drop=True)
    d0.iloc[CUT:].drop(columns="patient").rename(columns={"ts": "timestamp"}).to_csv(f"{ROOT}/data/samples/demo_new_cgm_batch.csv", index=False)
def seed(path):
    t = Twin(path, f"{ROOT}/models/glucose_tar_2h.joblib"); records.init(t.db); db = t.db
    for pid, age, summ in [("SYN-000", 27, "Synthetic demo patient. Oligomenorrhoea, acne, scalp hair thinning."), ("SYN-001", 31, "Synthetic patient with history only (no wearable data)."), ("SYN-002", 24, "Synthetic patient with no records yet (empty-state demo).")]:
        t.add_patient(pid); db.execute("INSERT OR IGNORE INTO profile(patient,age,sex,summary) VALUES(?,?,?,?)", (pid, age, "female", summ))
    H = [("diagnosis", "PCOS diagnosed by treating clinician (Rotterdam criteria, other causes excluded) - fictional", "2024-06-12", "clinician-confirmed"),
         ("medication", "Metformin 500 mg twice daily started - fictional", "2024-07-01", "clinician-confirmed"),
         ("family", "Mother: type 2 diabetes", "2024-06-12", "patient-reported"), ("history", "Acne since adolescence", "2024-06-12", "patient-reported")]
    for k, x, d, pv in H: db.execute("INSERT INTO history(patient,kind,text,date,provenance) VALUES('SYN-000',?,?,?,?)", (k, x, d, pv))
    starts = ["2025-05-03", "2025-06-14", "2025-07-30", "2025-08-31", "2025-10-22", "2025-11-26", "2026-01-19", "2026-02-24"]
    for s in starts: db.execute("INSERT INTO cycles(patient,start,end) VALUES('SYN-000',?,date(?, '+5 day'))", (s, s))
    for i, mth in enumerate(["2025-11", "2025-12", "2026-01", "2026-02"]):
        for kind, v in [("acne", [3, 3, 2, 2][i]), ("hair_thinning", [2, 2, 2, 1][i]), ("stress", [3, 4, 3, 2][i]), ("mood", [2, 2, 1, 1][i])]:
            for day in ("05", "20"): db.execute("INSERT INTO symptoms(patient,date,kind,severity,scale,notes) VALUES('SYN-000',?,?,?,?,NULL)", (f"{mth}-{day}", kind, v, "0-4 (0 none, 4 severe)"))
    M = [("Total Testosterone", 61, "ng/dL", "2025-11-10", "Hormone", 15, 70), ("Total Testosterone", 55, "ng/dL", "2026-01-12", "Hormone", 15, 70),
         ("Fasting Glucose", 98, "mg/dL", "2025-11-10", "Glycaemic/metabolic", 70, 99), ("Total Cholesterol", 214, "mg/dL", "2025-11-10", "Lipid", 0, 200),
         ("LDL Cholesterol", 136, "mg/dL", "2025-11-10", "Lipid", 0, 100), ("Systolic BP", 128, "mmHg", "2025-11-10", "Cardiovascular", None, None),
         ("Diastolic BP", 82, "mmHg", "2025-11-10", "Cardiovascular", None, None), ("Systolic BP", 124, "mmHg", "2026-01-12", "Cardiovascular", None, None), ("Diastolic BP", 80, "mmHg", "2026-01-12", "Cardiovascular", None, None)]
    for n, v, u, d, c, lo, hi in M: t.add_measurement("SYN-000", n, v, u, d, "synthetic seed (clinic record)", True, category=c, ref_low=lo, ref_high=hi)
    t.add_measurement("SYN-001", "Total Testosterone", 48, "ng/dL", "2026-02-02", "synthetic seed", True, category="Hormone", ref_low=15, ref_high=70)
    df = pd.read_csv(f"{ROOT}/data/synthetic_cgm.csv", parse_dates=["ts"]); d0 = df[df.patient == "SYN-000"].reset_index(drop=True)
    t.import_timeseries("SYN-000", d0.iloc[:CUT], "demo-batch-1", "synthetic generator (naari/synth.py, seed 7)")
    t.update("SYN-000", d0.ts[CUT-1], "initial seed"); records.init(db); db.commit(); return t
def reset(path=None):
    path = path or os.environ.get("NAARI_DB", f"{ROOT}/naari.db")
    if os.path.exists(path): os.remove(path)
    shutil.rmtree(f"{ROOT}/uploads", ignore_errors=True); os.makedirs(f"{ROOT}/uploads", exist_ok=True)
    make_sample_files(); return seed(path)
if __name__ == "__main__":
    reset(); print("Demo database reset (synthetic data).")
