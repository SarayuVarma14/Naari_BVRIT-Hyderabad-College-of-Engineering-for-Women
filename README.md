# NAARI — A PCOS-Focused Digital Twin Research Prototype

**Project title:** NAARI: A PCOS-Focused Digital Twin Research and Decision-Support Prototype  
**Team:** NAARI  
**Institution:** BVRIT Hyderabad College of Engineering for Women  
**Project status:** Working research prototype using synthetic demonstration data; not clinically validated

> **Important health and research limitation:** NAARI is a research prototype, not a medical device. It does not diagnose PCOS, prescribe treatment, or predict ovulation or fertility. Its glucose-event model is experimental, trained on synthetic data, and has not been clinically validated. Do not use its outputs to make healthcare decisions.

## Team Details

- **Namratha Raj Goud Palpanoor** — Team Leader
- **Priyanka K** — Team Member
- **Samskruthi Batikiri** — Team Member
- **Sarayu P** — Team Member

## College / Institution

**BVRIT Hyderabad College of Engineering for Women**

## Problem Statement

Information relevant to Polycystic Ovary Syndrome (PCOS)—including laboratory reports, verified clinical measurements, menstrual-cycle history, patient-reported symptoms, and physiological readings—can be distributed across different records and timelines. Reviewing these sources together and understanding how information changes over time can be difficult.

NAARI explores a prototype workflow for organising these records, reviewing and validating incoming information, maintaining a versioned Digital Twin snapshot, demonstrating a narrowly defined machine-learning prediction, and preparing a patient-specific summary for review.

## Healthcare Use Case

NAARI demonstrates how a reviewer can explore a fictional patient's available history in one application. The prototype can:

- Review uploaded laboratory reports and extracted fields before measurements are approved.
- Inspect verified laboratory measurements and the dates on which they were recorded.
- View recorded menstrual-cycle dates, cycle intervals, and patient-reported symptom history.
- Import and validate simulated continuous-glucose-monitoring (CGM), heart-rate, and step-count data from files.
- Inspect saved Digital Twin versions and compare changes to eligible Twin state.
- Run an experimental short-term glucose-event prediction when required inputs are available.
- Generate a patient-specific Doctor Brief that organises available information and clearly communicates limitations.

This prototype demonstrates an information-management and research workflow. It is **not a PCOS diagnostic system** and does not predict ovulation, fertility, or menstrual-cycle dates.

## Demo Video

**NAARI — PCOS Digital Twin Project Demonstration (20:23):**  
[Watch the unlisted YouTube demo](https://youtu.be/R0BbouP3k88)

The video is unlisted. Anyone with the link may be able to view and share it.

## System Architecture

- [System architecture diagram — PDF](docs/submission/NAARI_System_Architecture.pdf)
- [System architecture diagram — PowerPoint](docs/submission/NAARI_System_Architecture.pptx)

### Architecture overview

1. **Input sources:** clinical reports, laboratory results, verified measurements, menstrual-cycle dates, patient-reported symptoms, and simulated glucose/heart-rate/steps data.
2. **Review and validation:** report extraction and human review; checks for missing, duplicate, invalid, or out-of-range readings.
3. **Digital Twin:** saved versions contain eligible verified clinical measurements and a summary of recent valid glucose readings. Prior versions can be compared.
4. **Prediction:** a separate machine-learning model estimates a specific glucose-threshold event from recent physiological features and the latest verified HbA1c.
5. **Clinician-facing output:** the Doctor Brief summarises the selected patient's available information, relevant Twin details, prediction information, and limitations.

**Implementation boundary:** Menstrual-cycle and symptom records remain separate in this release; they are not direct inputs to the saved Twin snapshot or the glucose model. Heart rate and steps are model inputs but are not stored directly in the saved Twin state. Wearable-style readings are imported from files; the current prototype does not connect to live sensors.

## Project Presentation

- [Project presentation — PDF](docs/submission/NAARI_Project_Presentation.pdf)
- [Project presentation — PowerPoint](docs/submission/NAARI_Project_Presentation.pptx)

The presentation covers the problem and healthcare use case, system architecture, Digital Twin design, machine-learning task, prototype workflow, and limitations. It includes the team roster and corrected institution name.

## Technical Stack

- **Python** — backend and application logic.
- **JavaScript, HTML, and CSS** — interactive web interface.
- **SQLite** — local persistence for application records, audit information, and versioned Twin snapshots.
- **scikit-learn** — machine-learning model and evaluation workflow.
- **GradientBoostingClassifier** — primary glucose-event model.
- **Logistic regression** — baseline model for comparison.
- **joblib** — loading the saved model artifact.
- **pdfplumber, pytesseract, and Pillow** — report text extraction and OCR-related processing. Scanned-document OCR requires a local Tesseract installation.
- **ReportLab** — PDF generation in the application.

The dependency list is in [`requirements.txt`](requirements.txt).

## AI / ML Model Details

### Defined prediction task

The model estimates whether glucose will exceed **180 mg/dL within the next 120 minutes**, when the current glucose reading is at or below 180 mg/dL. This is a glucose-event prediction task, **not a PCOS prediction**.

### Inputs

The current feature set includes summaries of recent glucose readings, heart-rate mean, steps/activity, time of day, and the latest verified HbA1c. Menstrual-cycle records and symptom reports are not direct inputs to this model.

### Model and evaluation

- **Primary model:** `GradientBoostingClassifier` from scikit-learn.
- **Baseline:** logistic regression.
- **Evaluation measures shown in the application:** AUROC, AUPRC, precision, recall, and Brier score.
- The training/evaluation workflow uses a grouped patient split intended to reduce leakage between records from the same synthetic patient.
- Training and demonstration data are synthetic. Metrics from this workflow do not establish real-world clinical performance.

A displayed model score refers only to the defined glucose-event task. It is not a confirmed medical risk or a clinical recommendation. Further independent evaluation and suitable clinical validation would be required before any real-world healthcare use could be considered.

For additional implementation details, see [`MODEL_CARD.md`](MODEL_CARD.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Run the Prototype Locally

**Requirements:** Python 3.10 or newer is recommended. A local Tesseract installation is needed for OCR of scanned reports; core application functions can be explored without uploading scanned files.

### Windows PowerShell

Run these commands from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m naari.seed --reset
python -m naari.server
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Keep the terminal open while using the application.

**First-run note:** `python -m naari.seed --reset` creates/resets the local synthetic demo database and sample records. Run it on a fresh checkout for the initial demo setup; running it again resets the local demo state. Do not put a local database containing personal information or real patient records in a public repository.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m naari.seed --reset
python -m naari.server
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Demo Patients and Data

The included demonstration records are fictional and synthetic. They illustrate different levels of data availability, report review, time-series processing, Twin versioning, and prediction readiness. They are not real patient records and must not be interpreted as clinical evidence.

## Tests

From the repository root, run the core engine and API checks with:

```bash
python tests/test_core.py
python tests/test_api.py
```

Additional UI and browser-based tests are included in `tests/`. They require Playwright and an installed Chromium browser; some tests will not run until those browser prerequisites are installed.

## Limitations and Future Work

- The demonstration dataset and model training workflow use synthetic data.
- Wearable-style readings are imported from files; there is no live sensor connection in this release.
- The machine-learning model is experimental and has not been clinically validated.
- The application does not diagnose PCOS or predict ovulation, fertility, or cycle dates.
- Menstrual-cycle and symptom histories are kept separate from the current saved computational Twin snapshot and glucose model.
- Appropriate future work includes ethically governed research data, independent evaluation, calibration and generalisation studies, and clinical review before considering real-world use.

See [`REAL_DATA_STRATEGY.md`](REAL_DATA_STRATEGY.md) for the project's documented approach to responsible future data evaluation.

## Open-Source License

This project is released under the **MIT License**. See [`LICENSE`](LICENSE) for the full license text. The license applies to this project's code and materials as stated there; third-party packages and external assets remain subject to their own licenses and terms.

## Public Repository Checklist

Before submitting the repository:

- Confirm the GitHub repository visibility is set to **Public**.
- Confirm the README includes team and college details, problem statement, healthcare use case, technical stack, AI/ML details, demo-video link, architecture files, presentation files, and license details.
- Open the architecture PDF, presentation PDF, and YouTube demo link from a private/incognito browser window to verify public access.
- Keep `.venv`, caches, local databases, private environment files, and any real/personal patient records out of the public repository. The included `.gitignore` excludes the local database, uploads folder, environment file, and Python cache files.
