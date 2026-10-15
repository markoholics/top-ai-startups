// Dashboard data endpoint. Reads the v_* views from Supabase with the
// service role key. The key stays on the server and never reaches the browser.
//
// Env vars (set in Vercel):
//   SUPABASE_URL, SUPABASE_SERVICE_KEY   live data
//   DASHBOARD_PASSWORD                   required when live data is on
// With no Supabase vars set, the endpoint serves clearly labelled sample data.

const timingSafeEqual = require('crypto').timingSafeEqual;

const VIEWS = {
  coverage: 'v_coverage',
  coverageByAts: 'v_coverage_by_ats',
  openMatches: 'v_open_matches',
  hidden: 'v_hidden_by_location',
  scores: 'v_company_scores',
  events: 'v_recent_events',
  closed: 'v_closed_roles',
  manual: 'v_needs_manual_ats',
  nearMisses: 'v_near_misses',
  runs: 'v_run_history',
  digests: 'v_digest_history',
};

function sameString(a, b) {
  const x = Buffer.from(String(a));
  const y = Buffer.from(String(b));
  return x.length === y.length && timingSafeEqual(x, y);
}

function authorised(req, password) {
  const header = req.headers.authorization || '';
  if (!header.startsWith('Basic ')) return false;
  const decoded = Buffer.from(header.slice(6), 'base64').toString();
  const given = decoded.slice(decoded.indexOf(':') + 1);
  return sameString(given, password);
}

function sample() {
  const hoursAgo = (h) => new Date(Date.now() - h * 3600e3).toISOString();
  return {
    coverage: [
      { tier: 'seed', companies: 13, resolved_to_ats: 0, needs_manual: 0, verified: 0, not_yet_checked: 13 },
      { tier: 'india', companies: 0, resolved_to_ats: 0, needs_manual: 0, verified: 0, not_yet_checked: 0 },
    ],
    coverageByAts: [{ ats_type: 'none', companies: 13 }],
    openMatches: [
      { company: 'Sample Robotics', tier: 'seed', title: 'Product Marketing Manager', location: 'Bengaluru', ats_type: 'greenhouse', score: 100, seniority_flag: null, likely_repost: false, first_seen_at: hoursAgo(4), hours_since_first_seen: 4, source_tags: ['frontier_list', 'breakout_list'], apply_url: 'https://example.com/apply/1' },
      { company: 'Sample Labs', tier: 'india', title: 'Head of Growth Marketing', location: 'Remote India', ats_type: 'lever', score: 91.5, seniority_flag: null, likely_repost: false, first_seen_at: hoursAgo(30), hours_since_first_seen: 30, source_tags: ['india'], apply_url: 'https://example.com/apply/2' },
    ],
    hidden: [
      { company: 'Sample Robotics', tier: 'seed', title: 'GTM Manager', location: 'San Francisco', first_seen_at: hoursAgo(50), url: 'https://example.com/apply/3' },
    ],
    scores: [
      { company: 'Sample Robotics', tier: 'seed', score: 11.0, events: 1, last_event_at: hoursAgo(4), source_tags: ['frontier_list', 'breakout_list'] },
    ],
    events: [
      { observed_at: hoursAgo(4), company: 'Sample Robotics', tier: 'seed', event_type: 'matching_role_opened', weight: 10, detail: 'Product Marketing Manager, Bengaluru', evidence_url: 'https://example.com/apply/1', unsent: true },
    ],
    closed: [],
    manual: [{ name: 'Sample Unresolved Co', domain: null, tier: 'seed', careers_url: null, source_tags: [], ats_checked_at: null }],
    nearMisses: [{ company: 'Sample Labs', title: 'Marketing Analyst', best_match: 'marketing manager', score: 78, reason: 'below threshold', observed_at: hoursAgo(30) }],
    runs: [],
    digests: [],
  };
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  const password = process.env.DASHBOARD_PASSWORD;

  if (!url || !key) {
    // No live data. Sample data holds nothing private.
    return res.status(200).json({ demo: true, generatedAt: new Date().toISOString(), ...sample() });
  }
  if (!password) {
    // Fail closed. Never serve live data with no password set.
    return res.status(503).json({ error: 'DASHBOARD_PASSWORD is not set.' });
  }
  if (!authorised(req, password)) {
    res.setHeader('WWW-Authenticate', 'Basic realm="Job signals", charset="UTF-8"');
    return res.status(401).json({ error: 'Sign in required.' });
  }

  try {
    const base = url.replace(/\/+$/, '');
    const entries = await Promise.all(
      Object.entries(VIEWS).map(async ([name, view]) => {
        const r = await fetch(`${base}/rest/v1/${view}?select=*&limit=300`, {
          headers: { apikey: key, Authorization: `Bearer ${key}` },
        });
        if (!r.ok) throw new Error(`${view}: HTTP ${r.status}`);
        return [name, await r.json()];
      })
    );
    return res.status(200).json({ demo: false, generatedAt: new Date().toISOString(), ...Object.fromEntries(entries) });
  } catch (err) {
    return res.status(502).json({ error: String(err.message || err) });
  }
};
