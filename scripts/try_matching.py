"""Run the matcher on live postings. No database needed.
    python3 -I scripts/try_matching.py ashby:1x ashby:dyna-robotics greenhouse:figure
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.ats import ADAPTERS  # noqa: E402
from engine.matching import location_ok, match_title  # noqa: E402

total = matches = shown = hidden = 0
near = []
for arg in sys.argv[1:]:
    ats, _, token = arg.partition(":")
    jobs = ADAPTERS[ats].fetch_jobs(token)
    print(f"\n== {arg}: {len(jobs)} open roles")
    for j in jobs:
        total += 1
        t = match_title(j.title)
        if t.near_miss_reason:
            near.append((arg, j.title, t.best_include, t.score, t.near_miss_reason))
        if not t.is_match:
            continue
        matches += 1
        ok, why = location_ok(j.location, j.country, j.remote)
        tag = "SHOW  " if ok else "hidden"
        shown += ok
        hidden += (not ok)
        flag = f"  [{t.seniority_flag}]" if t.seniority_flag else ""
        print(f"  {tag} {t.score:5.1f}  {j.title}{flag}\n           {j.location or '(no location)'}  -> {why}\n           {j.url}")
print(f"\nTOTAL: {total} roles read, {matches} title matches, {shown} pass the location filter, {hidden} hidden by location")
print(f"\nNear misses ({len(near)}):")
for n in sorted(near, key=lambda x: -x[3]):
    print(f"  {n[3]:5.1f}  {n[1]}  (closest: {n[2]}; {n[4]})  [{n[0]}]")
