"""Report text extraction (PDF text layer, OCR fallback) + candidate lab-row parsing. Nothing is auto-approved."""
import re, io
from collections import defaultdict
LINE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9 ,\-\(\)/\.]*?)\s+(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>%|[A-Za-z][^\s]*)(?:\s+(?P<lo>\d+(?:\.\d+)?)\s*[-\u2013]\s*(?P<hi>\d+(?:\.\d+)?))?\s*(?P<flag>High|Low|H|L)?\s*$")
DATE = re.compile(r"(?:collected|sample date|date|reported)\D{0,15}(20\d\d-\d\d-\d\d)", re.I)
CATS = [("testosterone|dheas|androstenedione|shbg|\\blh\\b|\\bfsh\\b|oestradiol|estradiol", "Hormone"), ("prolactin|tsh|thyroxine|\\bt3\\b|\\bt4\\b", "Thyroid/other endocrine"),
        ("hba1c|glucose|ogtt|insulin", "Glycaemic/metabolic"), ("cholesterol|ldl|hdl|triglycer", "Lipid"), ("systolic|diastolic|blood pressure", "Cardiovascular"), ("amh|ultrasound", "Other diagnostic")]
def category(name):
    for rx, c in CATS:
        if re.search(rx, name, re.I): return c
    return "Other"
def parse_lines(text, page, method, confs=None):
    date = next((m.group(1) for m in [DATE.search(text)] if m), None); out = []
    for i, ln in enumerate(text.splitlines()):
        m = LINE.match(ln.strip())
        if m:
            d = m.groupdict()
            out.append({"page": page, "name": d["name"].strip(), "value": d["val"], "unit": d["unit"], "date": date, "ref_low": d["lo"], "ref_high": d["hi"],
                        "flag": d["flag"], "method": method, "conf": (confs or {}).get(ln.strip()), "raw": ln.strip()})
    return out
def _ocr(img):
    import pytesseract
    from pytesseract import Output
    d = pytesseract.image_to_data(img, output_type=Output.DICT); lines = defaultdict(list)
    for i, w in enumerate(d["text"]):
        if w.strip(): lines[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append((w, float(d["conf"][i])))
    txt, confs = [], {}
    for k in sorted(lines):
        s = " ".join(w for w, _ in lines[k]); txt.append(s); cs = [c for _, c in lines[k] if c >= 0]
        if cs: confs[s] = round(sum(cs)/len(cs), 1)        # mean Tesseract word confidence (0-100)
    return "\n".join(txt), confs
def extract(path, kind):
    """returns dict(text, pages, method, fields, error)"""
    try:
        if kind == "pdf":
            import pdfplumber
            texts, fields, methods = [], [], set()
            with pdfplumber.open(path) as pdf:
                for n, pg in enumerate(pdf.pages, 1):
                    t = pg.extract_text() or ""; confs = None; m = "pdf-text-layer"
                    if len(t.strip()) < 20:
                        t, confs = _ocr(pg.to_image(resolution=200).original); m = "ocr-tesseract"
                    methods.add(m); texts.append(t); fields += parse_lines(t, n, m, confs)
                return {"text": "\n\n".join(texts), "pages": len(texts), "method": "+".join(sorted(methods)), "fields": fields, "error": None}
        if kind in ("png", "jpg"):
            from PIL import Image
            t, confs = _ocr(Image.open(path)); return {"text": t, "pages": 1, "method": "ocr-tesseract", "fields": parse_lines(t, 1, "ocr-tesseract", confs), "error": None}
    except Exception as e:
        return {"text": "", "pages": 0, "method": None, "fields": [], "error": f"{type(e).__name__}: {e}"}
    return {"text": "", "pages": 0, "method": None, "fields": [], "error": "unsupported kind"}
