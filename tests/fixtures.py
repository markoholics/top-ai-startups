"""Fixture data for the digest. Fake companies, fake links. Never real roles."""
import datetime as dt

NOW = dt.datetime(2026, 10, 15, 13, 0, tzinfo=dt.timezone.utc)      # 18:30 IST


def hours(n):
    return NOW - dt.timedelta(hours=n)


NEW_ROLES = [
    {"event_id": "e1", "company": "Fixture Robotics", "tier": "seed", "title": "Product Marketing Manager",
     "location": "Bengaluru", "ats_type": "ashby", "first_seen_at": hours(4), "url": "https://example.com/fixture-robotics/pmm",
     "score": 100, "company_score": 14.0, "source_tags": ["frontier_list", "breakout_list"]},
    {"event_id": "e2", "company": "Sample Labs", "tier": "india", "title": "Director of Marketing",
     "location": "Remote India", "ats_type": "lever", "first_seen_at": hours(9), "url": "https://example.com/sample-labs/dom",
     "score": 95, "company_score": 10.0, "source_tags": ["india"], "seniority_flag": "senior, check fit"},
    {"event_id": "e3", "company": "Test Intelligence", "tier": "seed", "title": "GTM Manager",
     "location": "Remote APAC", "ats_type": "greenhouse", "first_seen_at": hours(1), "url": "https://example.com/test-intelligence/gtm",
     "score": 100, "company_score": 9.0, "source_tags": ["cosign"]},
]
MOVED = [
    {"event_id": "e4", "company": "Demo Bots", "tier": "seed", "event_type": "first_marketing_role_posted",
     "detail": "Marketing Operations Manager (Bengaluru)", "evidence_url": "https://example.com/demo-bots/role", "company_score": 8.0},
    {"event_id": "e5", "company": "Placeholder AI", "tier": "india", "event_type": "open_role_count_jump",
     "detail": "open roles 12 to 18", "evidence_url": "https://example.com/placeholder/careers", "company_score": 3.0},
]
CLOSED = [{"event_id": "e6", "company": "Fixture Robotics", "title": "Marketing Manager", "location": "Bengaluru",
           "closed_at": hours(7)}]
MANUAL = [{"name": "Unresolved Example Co", "tier": "seed", "careers_url": "https://example.com/careers", "note": "Page builds its links with JavaScript."},
          {"name": "Another Example Co", "tier": "india"}]
FAILED = ["Harmonic Hot 25: blocked automated access. Used the hand kept CSV instead."]
