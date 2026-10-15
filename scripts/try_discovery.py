"""Try discovery on names from the command line. Needs no database.
    python3 -I scripts/try_discovery.py "Skild AI" "Figure"
Add a domain with a pipe: "Apollo|apollofirst.xyz"
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.discover import discover  # noqa: E402

for arg in sys.argv[1:]:
    name, _, domain = arg.partition("|")
    r = discover(name, domain or None)
    if r["ats_type"] is None:
        print(f"{name:26} NOTHING   {'; '.join(r['notes'] + r['errors'])}")
        continue
    extra = f" (board says: {r.get('reported_name')})" if r["confidence"] == "unconfirmed" else ""
    print(f"{name:26} {r['confidence'].upper():11} {r['ats_type']}:{r['ats_token']}{extra}  open roles: {len(r['jobs'])}")
    for n in r["notes"]:
        print(f"{'':26} note: {n}")
