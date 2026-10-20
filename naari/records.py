"""Extra tables: users, documents, extracted fields, cycles, symptoms, history, profile."""
import hashlib, os, secrets
SCHEMA2 = """
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, salt TEXT, pw_hash TEXT, role TEXT);
CREATE TABLE IF NOT EXISTS profile(patient TEXT PRIMARY KEY, age INT, sex TEXT, summary TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, patient TEXT, kind TEXT, text TEXT, date TEXT, provenance TEXT);
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, patient TEXT, filename TEXT, stored TEXT, kind TEXT, uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
  status TEXT, method TEXT, error TEXT, text TEXT, pages INT);
CREATE TABLE IF NOT EXISTS fields(id INTEGER PRIMARY KEY, document_id INT REFERENCES documents(id), page INT, name TEXT, value TEXT, unit TEXT, date TEXT,
  ref_low TEXT, ref_high TEXT, flag TEXT, method TEXT, conf REAL, status TEXT DEFAULT 'pending', measurement_id INT, raw TEXT);
CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY, patient TEXT, start TEXT, end TEXT, notes TEXT, UNIQUE(patient,start));
CREATE TABLE IF NOT EXISTS symptoms(id INTEGER PRIMARY KEY, patient TEXT, date TEXT, kind TEXT, severity REAL, scale TEXT, notes TEXT);
"""
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt, hashlib.scrypt(pw.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()
NAMES = {"SYN-000": "Ananya Rao", "SYN-001": "Meera Sharma", "SYN-002": "Kavya Reddy"}   # fictional display names; IDs unchanged
def init(db):
    db.executescript(SCHEMA2)
    if "name" not in [r[1] for r in db.execute("PRAGMA table_info(profile)")]: db.execute("ALTER TABLE profile ADD COLUMN name TEXT")
    for k, v in NAMES.items(): db.execute("UPDATE profile SET name=? WHERE patient=? AND (name IS NULL OR name='')", (v, k))
    db.commit()
    if not db.execute("SELECT 1 FROM users").fetchone():
        salt, h = hash_pw(os.environ.get("NAARI_DEMO_PASSWORD", "naari-demo"))
        db.execute("INSERT INTO users VALUES('clinician',?,?,'clinician')", (salt, h)); db.commit()
