# NAARI real-data strategy: what can be used, and what it would prove

## Short answer
Yes, real-world research datasets may be usable. However, a website describing a dataset is not the same as granting unrestricted access or proving that the data fits NAARI's prediction task. The delivered NAARI model is still trained on synthetic data. This plan does not silently replace it with a different model or insert external records into the demo database.

## Candidate datasets

### 1. mcPHASES — best candidate to investigate for menstrual-health research
- Source: PhysioNet dataset page: https://physionet.org/content/mcphases/1.0.0/
- Paper/data descriptor: https://www.nature.com/articles/s41597-026-06805-3
- Description: 42 consenting participants contributed a three-month observation period; 20 participated in a second three-month interval. The dataset combines self-reported menstruation, hormones, wearable physiology, CGM and symptoms.
- Access: PhysioNet labels this resource as restricted. The team must sign in, read and comply with the dataset's current Data Use Agreement and access requirements. Do not bypass access controls or redistribute the raw dataset.
- Fit: useful to prototype/evaluate menstrual-cycle or menstrual-health methods and to explore links between cycle records and physiological signals. Dates are represented as relative study days in the released dataset, so it is more naturally suited to predicting an interval/day-in-study than a person's real calendar date.
- Limitation: it is a small menstrual-health cohort, not a PCOS-specific clinical cohort. The paper reports exclusion of participants with diabetes, and data release is limited to people who specifically consented. It cannot by itself validate a PCOS-specific model.

### 2. BIG IDEAs Lab Glycemic Variability and Wearable Device Data — possible external glucose research dataset
- Source: https://physionet.org/content/big-ideas-glycemic-wearable/ (verify the current release before downloading).
- Description: 16 participants with elevated but generally high-normal glucose were monitored for 8–10 days with Dexcom G6 CGM and Empatica E4 wearable signals. The PhysioNet page labels the data open-access under the Open Data Commons Attribution License 1.0.
- Fit: useful for testing data ingestion and exploring glucose/wearable features on actual measurements under the license terms.
- Limitation: it is small and not a PCOS cohort; it may not provide every exact NAARI feature, especially the verified HbA1c and step summaries as currently engineered. The archive is large. A compatible external evaluation requires careful preprocessing and likely a separate reduced-feature model. Do not simply feed its columns into the existing 12-feature model or call this clinical validation.

### 3. Publicly shared PCOS clinical dataset — useful only for a different question
- Source: https://www.kaggle.com/datasets/prasoonkottarathil/polycystic-ovary-syndrome-pcos
- Published research describes a dataset of about 541 records originally collected across hospitals in Kerala, with demographic, clinical, menstrual-history, laboratory and ultrasound features. One analysis notes that verifiable diagnostic criteria for the PCOS label are not provided.
- Fit: potentially useful for exploratory, retrospective analyses of static PCOS-related features after checking the dataset provenance and applicable licence.
- Limitation: the dataset is cross-sectional rather than a timestamped sequence of cycle starts or wearable streams. It is not appropriate for training a next-cycle-date or two-hour glucose-excursion predictor by itself. Do not build or market a diagnostic classifier solely because a Kaggle file has a `PCOS` label.

### 4. Large commercial menstrual-cycle datasets
A published study analysed more than 600,000 menstrual cycles, but its data-availability statement says the underlying data are not publicly available and reuse requires permission from Natural Cycles/the authors. A paper reporting a large dataset is not a download licence. Source: https://www.nature.com/articles/s41746-019-0152-7

## Why not put real records straight into the delivered demo?
1. Privacy and permission: real human health data must only be used within its consent, licence, Data Use Agreement and ethical requirements. De-identification lowers risk but does not remove every obligation.
2. Task fit: static diagnosis/lab tables cannot answer a longitudinal forecasting question without the right dates, repeated measurements and target labels.
3. Model compatibility: NAARI's glucose model expects a specific 12-feature vector. Different data formats, sensor types or missing HbA1c/steps cannot be silently substituted.
4. Sample size and representativeness: a few participants or an ordinary-population menstrual dataset do not establish performance in people with PCOS.
5. Reproducible demo: the synthetic seeded patients allow every evaluator to reproduce the same screens. External data belongs in a separate research/evaluation path, not mixed into fictional patient profiles.

## Recommended implementation path
1. Keep the current seeded demo and current glucose model clearly labelled synthetic.
2. Investigate access to mcPHASES for cycle-method experiments. Only proceed once the team accepts the access conditions.
3. Evaluate a statistical median-cycle baseline first. Compare it against a candidate ML method only with participant-grouped temporal splits and honest metrics (e.g. absolute error in days), avoiding leakage across the same participant's cycles. Because current NAARI demo cycles are synthetic, they only demonstrate the baseline UI, not generalisation.
4. Separately investigate BIG IDEAs for glucose external evaluation. Map raw columns to model features, document all transformations, compare only on compatible inputs, and report the small sample limitation. If the current 12 features do not exist, train/evaluate a separate model rather than faking features.
5. Keep a manifest recording dataset name, version, source URL, retrieval date, licence/DUA, checksum, fields used, exclusions, transformations and split by participant. Never put direct identifiers into the app.
6. Only describe a model as validated on real data after a documented real-data evaluation has actually run. For clinical use, additional external validation, clinical governance and appropriate ethics/regulatory review would be needed.

## What is implemented today
- The saved glucose-excursion Gradient Boosting model: trained on synthetic data only.
- The cycle page: a statistical median-interval estimate based on recorded periods; this is not ML. If the estimated date is already past, the page labels it a historical estimate rather than a current forecast.
- No external real-data model has been trained or bundled in this release.
