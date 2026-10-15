"""Send the fixture digest as a test. Fake companies, fake links.
    python3 -I scripts/send_test_digest.py --dry-run     print it, send nothing
    python3 -I scripts/send_test_digest.py               send it (needs SMTP_USER, SMTP_PASSWORD, NOTIFY_TO)
    python3 -I scripts/send_test_digest.py --baseline    send the baseline style email
"""
import os
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
import fixtures as fx  # noqa: E402
from engine.digest import build_baseline, build_digest  # noqa: E402
from engine.mail import get_mailer  # noqa: E402

if "--baseline" in sys.argv:
    d = build_baseline(fx.NEW_ROLES, fx.MANUAL, fx.FAILED, 12, fx.NOW)
else:
    d = build_digest(fx.NEW_ROLES, fx.MOVED, fx.CLOSED, fx.MANUAL, fx.FAILED, 12, fx.NOW)
subject = "[TEST] " + d.subject
if "--dry-run" in sys.argv:
    print("SUBJECT:", subject, "\n")
    print(d.text)
    sys.exit(0)
to = os.environ.get("NOTIFY_TO", "")
banner = "THIS IS A TEST WITH FAKE DATA. No real company or role is listed.\n\n"
get_mailer().send(to, subject, banner + d.text, "<p><b>THIS IS A TEST WITH FAKE DATA.</b> No real company or role is listed.</p>" + d.html)
print("Test email sent.")
