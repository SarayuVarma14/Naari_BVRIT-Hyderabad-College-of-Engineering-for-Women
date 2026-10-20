"""Regression test: date labels must not use Unix-only strftime %-d directive."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from naari.server import D, DT
source = (ROOT / "naari" / "server.py").read_text(encoding="utf-8")
assert 'strftime("%-d' not in source and "strftime('%-d" not in source, "Unix-only %-d format remains in server.py"
assert D("2026-03-08") == "8 Mar 2026", D("2026-03-08")
assert D("2026-11-01") == "1 Nov 2026", D("2026-11-01")
assert DT("2026-03-14T23:55:00Z") == "14 Mar 2026 23:55 UTC", DT("2026-03-14T23:55:00Z")
assert DT("2026-03-14T18:55:00-05:00") == "14 Mar 2026 23:55 UTC", DT("2026-03-14T18:55:00-05:00")
print("DATE FORMAT PORTABILITY TESTS PASSED")
