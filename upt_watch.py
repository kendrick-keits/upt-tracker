#!/usr/bin/env python3
"""
UPT Job Watcher for MilRecruiter

MilRecruiter has no public API, so this reads its public pages (robots.txt
allows these):
  1. https://app.milrecruiter.com/squadrons  - every squadron and whether it is
     "Currently Accepting Applications" for UPT
  2. /squadrons/<name> for each squadron accepting UPT - its open jobs with
     aircraft, application deadline and job link

Sends a phone alert for each new UPT posting and rebuilds a web page of all
open UPT postings. Rated postings are ignored.

Standard library only - no pip installs needed.

Usage:
  python upt_watch.py                 # fetch live, alert, build page
  python upt_watch.py --no-notify     # skip push notifications
  python upt_watch.py --squadrons-html F --pages-dir D   # offline test
                                      # (D holds <squadron-slug>.html files)

Environment variables (all optional):
  NTFY_TOPIC    ntfy.sh topic for phone push alerts (e.g. kendrick-upt-7q2m9x)
  NTFY_SERVER   defaults to https://ntfy.sh
  SITE_URL      link opened when you tap an alert
  SITE_DOMAIN   custom domain for GitHub Pages, e.g. jobs.peanutsandalmonds.com
  DELAY         seconds between squadron page requests (default 1.5)
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://app.milrecruiter.com"
SQUADRONS_URL = BASE_URL + "/squadrons"
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "state.json"
SITE_DIR = ROOT / "site"
USER_AGENT = "Mozilla/5.0 (personal UPT job watcher; checks every few hours)"
JOB_TYPE = "UPT"


# --------------------------------------------------------------------------
# Fetching & parsing
# --------------------------------------------------------------------------

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def _chunks(page: str, start_pattern: str, end_marker: str | None = None) -> list[str]:
    starts = [m.start() for m in re.finditer(start_pattern, page)]
    out = []
    for s, e in zip(starts, starts[1:] + [len(page)]):
        chunk = page[s:e]
        if end_marker and end_marker in chunk:
            chunk = chunk[: chunk.index(end_marker) + len(end_marker)]
        out.append(chunk)
    return out


def parse_squadrons(page: str) -> list[dict]:
    """Every squadron on the directory page, with its hiring status."""
    squadrons = []
    for li in _chunks(page, r"<li[^>]*data-squadron-id=", "</li>"):
        sid = re.search(r'data-squadron-id="(\d+)"', li).group(1)
        name = re.search(r'<h2[^>]*>\s*<a[^>]*href="/squadrons/([^"]+)"[^>]*>(.*?)</a>', li, re.S)
        wing_comp = re.search(r'data-test="wing-and-component"[^>]*>(.*?)</p>', li, re.S)
        wing, _, component = _text(wing_comp.group(1)).rpartition(" | ") if wing_comp else ("", "", "")
        info = li.split('data-test="follow-toggle"')[0]
        paras = [_text(p) for p in re.findall(r"<p(?![^>]*data-test)[^>]*>(.*?)</p>", info, re.S)]
        aircraft = next((p.split(":", 1)[1].strip() for p in paras if p.startswith("Aircraft:")), "")
        location = next((p for p in paras if not p.startswith("Aircraft:")), "")

        def accepting(kind: str) -> bool:
            block = re.search(rf'id="{kind}-positions-{sid}"(.*?)</div>\s*<span', li, re.S)
            return bool(block and "Currently Accepting" in block.group(1))

        slug = name.group(1) if name else ""
        squadrons.append({
            "id": sid,
            "slug": slug,
            "name": _text(name.group(2)) if name else f"Squadron {sid}",
            "wing": wing,
            "component": component,
            "location": location,
            "aircraft": aircraft,
            "UPT": accepting("upt"),
            "RATED": accepting("rated"),
            "url": f"{SQUADRONS_URL}/{slug}" if slug else SQUADRONS_URL,
        })
    return squadrons


def parse_squadron_jobs(page: str) -> list[dict]:
    """Open jobs listed on one squadron's page."""
    section = page.split('id="squadron_job_postings"', 1)
    if len(section) < 2:
        return []
    jobs = []
    for card in _chunks(section[1], r'<div id="job_posting_\d+"'):
        jid = re.search(r'id="job_posting_(\d+)"', card).group(1)

        def field(label: str) -> str:
            m = re.search(rf">\s*{label}\s*</p>\s*<p[^>]*>(.*?)</p>", card, re.S)
            return _text(m.group(1)) if m else ""

        link = re.search(r'href="(/squadrons/[^"]+/job_postings/\d+)"', card)
        jobs.append({
            "id": jid,
            "type": field("Type").split(" ")[0].upper(),
            "aircraft": field("Aircraft"),
            "deadline": field("Application Deadline"),
            "url": BASE_URL + link.group(1) if link else "",
        })
    return jobs


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        state.setdefault("jobs", {})
        state.setdefault("squadrons", {})
        return state
    return {"squadrons": {}, "jobs": {}, "last_checked": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def update_state(state, squadrons, jobs_by_slug, failed_slugs, now) -> list[str]:
    alerts = []
    first_run = state["last_checked"] is None
    seen_ids = set()

    for sq in squadrons:
        old_sq = state["squadrons"].get(sq["id"], {})
        sq_jobs = [j for j in jobs_by_slug.get(sq["slug"], []) if j["type"] == JOB_TYPE]

        for job in sq_jobs:
            seen_ids.add(job["id"])
            rec = state["jobs"].get(job["id"])
            info = {**job, "squadron": sq["name"], "squadron_url": sq["url"], "slug": sq["slug"],
                    "component": sq["component"], "location": sq["location"], "wing": sq["wing"]}
            if rec is None:
                state["jobs"][job["id"]] = {**info, "first_seen": now, "last_seen": now, "active": True}
                if not first_run:
                    alerts.append(
                        f"NEW {job['type']}: {job['aircraft']} - {sq['name']} ({sq['component']}), "
                        f"{sq['location']}. Deadline {job['deadline'] or 'not listed'}. {job['url']}"
                    )
            else:
                if rec.get("deadline") != job["deadline"] and rec.get("active"):
                    alerts.append(
                        f"DEADLINE CHANGED {job['type']}: {sq['name']} "
                        f"{rec.get('deadline')} -> {job['deadline']}. {job['url']}"
                    )
                rec.update({**info, "last_seen": now, "active": True})
                rec.pop("closed_on", None)

        # Fallback: squadron says it's accepting UPT but no UPT posting was listed.
        if sq["UPT"] and not old_sq.get("UPT") and not first_run \
                and sq["slug"] not in failed_slugs and not sq_jobs:
            alerts.append(f"UPT OPEN: {sq['name']} ({sq['component']}), {sq['location']} - "
                          f"{sq['aircraft']}. {sq['url']}")
        state["squadrons"][sq["id"]] = {**sq, "last_seen": now}

    # Postings that disappeared (skip squadrons whose page failed to load this run).
    for rec in state["jobs"].values():
        if rec.get("type") != JOB_TYPE:
            rec["active"] = False  # drop any non-UPT records from older versions
        elif rec.get("active") and rec["id"] not in seen_ids and rec.get("slug") not in failed_slugs:
            rec["active"] = False
            rec["closed_on"] = now

    state["last_checked"] = now
    return alerts


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

def notify(alerts: list[str]) -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not alerts:
        return
    if not topic:
        print("(NTFY_TOPIC not set - alerts printed only)")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(
        f"{server}/{topic}", data="\n\n".join(alerts).encode("utf-8"), method="POST",
        headers={
            "Title": f"MilRecruiter: {len(alerts)} UPT update(s)",
            "Tags": "airplane",
            "Click": os.environ.get("SITE_URL", SQUADRONS_URL),
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"Push sent ({resp.status})")


# --------------------------------------------------------------------------
# Web page
# --------------------------------------------------------------------------

def _deadline(job: dict) -> dt.date | None:
    try:
        return dt.datetime.strptime(job.get("deadline", ""), "%b %d, %Y").date()
    except ValueError:
        return None


def _date(iso: str | None) -> dt.date | None:
    try:
        return dt.datetime.fromisoformat(iso).date()
    except (TypeError, ValueError):
        return None


def _card(job: dict, today: dt.date, baseline: dt.date) -> str:
    e = html.escape
    first = _date(job.get("first_seen"))
    is_new = first and first > baseline and (today - first).days <= 3
    d = _deadline(job)
    if d:
        left = (d - today).days
        cls = " soon" if 0 <= left <= 14 else ""
        when = f'<span class="dl{cls}"><b>{d:%b %d, %Y}</b> &middot; {left} days left</span>'
    else:
        when = f'<span class="dl"><b>{e(job.get("deadline") or "Not listed")}</b></span>'
    return f"""
      <article class="job" data-comp="{e(job['component'])}">
        <div class="top">
          <span class="tag comp">{e(job['component'])}</span>
          {'<span class="tag new">NEW</span>' if is_new else ''}
        </div>
        <h3><a href="{e(job['squadron_url'])}" target="_blank" rel="noopener">{e(job['squadron'])}</a></h3>
        <p class="meta">&#9992; {e(job['aircraft'])} &nbsp;&middot;&nbsp; &#128205; {e(job['location'])}</p>
        <p class="deadline">Application deadline: {when}</p>
        <a class="btn" href="{e(job['url'])}" target="_blank" rel="noopener">View job &rarr;</a>
      </article>"""


def build_site(state: dict) -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    e = html.escape
    now = dt.datetime.fromisoformat(state["last_checked"])
    today = now.date()
    jobs = list(state["jobs"].values())
    baseline = min((_date(j.get("first_seen")) for j in jobs), default=today)

    active = sorted([j for j in jobs if j.get("active")],
                    key=lambda j: (_deadline(j) or dt.date.max, j["squadron"]))
    closed = sorted([j for j in jobs if not j.get("active")],
                    key=lambda j: j.get("closed_on", ""), reverse=True)[:25]
    n_sq = len({j["slug"] for j in active})
    n_ang = sum(j["component"] == "Air National Guard" for j in active)
    n_afr = sum(j["component"] == "Air Force Reserve" for j in active)

    cards = "".join(_card(j, today, baseline) for j in active) or '<p class="empty">No open postings right now.</p>'
    closed_html = "".join(
        f"<li>{e(j['squadron'])} ({e(j['aircraft'])}, {e(j['location'])}) &mdash; deadline {e(j['deadline'])}, "
        f"removed {_date(j.get('closed_on')):%b %d}</li>" for j in closed
    ) or "<li>None yet.</li>"

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UPT Job Tracker &middot; Peanuts &amp; Almonds</title>
<style>
  :root {{ --bg:#0d1b2a; --panel:#1b263b; --line:#33415c; --ink:#e0e1dd; --mute:#9aa5b1; --accent:#f4a261; --ok:#2a9d8f; --warn:#e76f51; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); line-height:1.5; }}
  header, main {{ max-width:960px; margin:auto; padding:0 1.25rem; }}
  header {{ padding-top:2.5rem; padding-bottom:1.25rem; }}
  header h1 {{ margin:0; font-size:2rem; }}
  header p {{ margin:.35rem 0 0; color:var(--mute); }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1rem; margin-bottom:1.25rem; }}
  .stat {{ background:var(--panel); border-radius:10px; padding:.9rem 1.2rem; }}
  .stat b {{ display:block; font-size:1.8rem; color:var(--accent); }}
  .stat span {{ color:var(--mute); font-size:.9rem; }}
  .filters {{ display:flex; gap:.5rem; margin-bottom:.6rem; flex-wrap:wrap; align-items:center; }}
  .filters small {{ color:var(--mute); width:4.5rem; }}
  .filters button {{ background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:999px; padding:.3rem .85rem; cursor:pointer; font:inherit; font-size:.85rem; }}
  .filters button.on {{ background:var(--accent); color:#1b263b; border-color:var(--accent); font-weight:600; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:1rem; margin-top:1rem; }}
  .job {{ background:var(--panel); border-radius:12px; padding:1rem 1.15rem; border-top:3px solid var(--accent); display:flex; flex-direction:column; }}
  .job[data-comp="Air Force Reserve"] {{ border-top-color:var(--ok); }}
  .top {{ display:flex; gap:.35rem; flex-wrap:wrap; }}
  .tag {{ font-size:.7rem; font-weight:700; padding:.12rem .5rem; border-radius:4px; }}
  .comp {{ background:rgba(154,165,177,.15); color:var(--mute); }}
  .new {{ background:var(--ok); color:#fff; }}
  .job h3 {{ margin:.55rem 0 0; font-size:1.1rem; }}
  .job h3 a {{ color:var(--ink); text-decoration:none; }}
  .job h3 a:hover {{ color:var(--accent); }}
  .meta {{ margin:.15rem 0 0; color:var(--mute); font-size:.9rem; }}
  .deadline {{ margin:.55rem 0 0; font-size:.88rem; color:var(--mute); }}
  .dl b {{ color:var(--ink); }}
  .dl.soon b {{ color:var(--warn); }}
  .btn {{ margin-top:auto; padding-top:.6rem; color:var(--accent); text-decoration:none; font-weight:600; font-size:.9rem; }}
  .btn:hover {{ text-decoration:underline; }}
  h2 {{ font-size:1rem; color:var(--mute); text-transform:uppercase; letter-spacing:.08em; margin-top:2.25rem; }}
  ul.past {{ color:var(--mute); padding-left:1.1rem; font-size:.88rem; }}
  ul.past li {{ margin-bottom:.25rem; }}
  .empty {{ color:var(--mute); }}
  footer {{ text-align:center; color:var(--mute); font-size:.8rem; padding:2rem 0; }}
  footer a {{ color:var(--mute); }}
</style>
</head>
<body>
<header>
  <h1>&#9992; UPT Job Tracker</h1>
  <p>Open Undergraduate Pilot Training postings from Guard and Reserve squadrons on MilRecruiter, soonest deadline first.</p>
</header>
<main>
  <div class="stats">
    <div class="stat"><b>{len(active)}</b><span>UPT postings ({n_sq} squadrons)</span></div>
    <div class="stat"><b>{n_ang}</b><span>Air National Guard</span></div>
    <div class="stat"><b>{n_afr}</b><span>Air Force Reserve</span></div>
    <div class="stat"><b>{now:%b %d}</b><span>last checked {now:%H:%M} UTC</span></div>
  </div>
  <div class="filters" data-key="comp">
    <small>Component</small>
    <button class="on" data-v="">All</button><button data-v="Air National Guard">Air National Guard</button><button data-v="Air Force Reserve">Air Force Reserve</button>
  </div>
  <div class="grid">{cards}</div>
  <h2>Recently removed UPT postings</h2>
  <ul class="past">{closed_html}</ul>
</main>
<footer>Data from the public <a href="{SQUADRONS_URL}">MilRecruiter squadron pages</a> &middot; {len(state['squadrons'])} squadrons tracked &middot; peanutsandalmonds.com</footer>
<script>
  const f = {{ comp: "" }};
  document.querySelectorAll(".filters").forEach(group => group.querySelectorAll("button").forEach(b => b.onclick = () => {{
    group.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    f[group.dataset.key] = b.dataset.v;
    document.querySelectorAll(".job").forEach(c => c.hidden = !!f.comp && c.dataset.comp !== f.comp);
  }}));
</script>
</body>
</html>
"""
    (SITE_DIR / "index.html").write_text(page)
    (SITE_DIR / "jobs.json").write_text(json.dumps(
        {"last_checked": state["last_checked"], "open": active, "recently_removed": closed}, indent=2))
    domain = os.environ.get("SITE_DOMAIN", "").strip()
    if domain:
        (SITE_DIR / "CNAME").write_text(domain + "\n")


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--squadrons-html", help="saved squadrons directory page (offline test)")
    ap.add_argument("--pages-dir", help="folder of saved squadron pages named <slug>.html (offline test)")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()

    delay = float(os.environ.get("DELAY", "1.5"))
    offline = bool(args.squadrons_html)

    squadrons = parse_squadrons(Path(args.squadrons_html).read_text() if offline else fetch(SQUADRONS_URL))
    if not squadrons:
        print("ERROR: no squadrons found - the site layout may have changed.", file=sys.stderr)
        return 1

    hiring = [s for s in squadrons if s["UPT"]]
    jobs_by_slug, failed = {}, set()
    for i, sq in enumerate(hiring):
        try:
            if offline:
                p = Path(args.pages_dir or ".") / f"{sq['slug']}.html"
                page = p.read_text() if p.exists() else ""
            else:
                if i:
                    time.sleep(delay)
                page = fetch(sq["url"])
            jobs_by_slug[sq["slug"]] = parse_squadron_jobs(page)
        except Exception as exc:  # noqa: BLE001 - one bad page shouldn't sink the run
            failed.add(sq["slug"])
            print(f"(could not read {sq['url']}: {exc})")

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    state = load_state()
    first_run = state["last_checked"] is None
    alerts = update_state(state, squadrons, jobs_by_slug, failed, now)
    save_state(state)
    build_site(state)

    active = [j for j in state["jobs"].values() if j.get("active")]
    print(f"{len(squadrons)} squadrons, {len(hiring)} accepting UPT; {len(active)} open UPT postings")
    if first_run:
        print("First run: saved current postings as the baseline (no alerts sent).")
    for a in alerts:
        print(" -", a)
    if not args.no_notify:
        notify(alerts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
