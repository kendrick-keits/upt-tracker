# UPT Job Watcher

MilRecruiter has no public API, so this reads its public pages every 3 hours (its robots.txt allows these pages):
- **https://app.milrecruiter.com/squadrons** shows about 170 ANG and Reserve squadrons and whether each one is accepting UPT applications.
- **Each squadron accepting UPT** has a page listing its open jobs with aircraft, application deadline and a link. Rated postings are ignored.

The watcher does three things:
1. Tracks every open UPT posting by its job ID.
2. Sends a **push notification to your phone** for each new UPT posting or deadline change.
3. Publishes a **web page** of all open UPT postings, soonest deadline first. It has ANG/Reserve filters and lists postings that were recently removed. For example, it can live at `jobs.peanutsandalmonds.com`.

It runs free on GitHub Actions and hosts the page free on GitHub Pages. You don't need a server, and your PC doesn't have to be on.

## Setup (about 15 minutes)

### 1. Phone alerts (ntfy, free)
- Install the **ntfy** app (iOS or Android).
- Subscribe to a topic name that's hard to guess, e.g. `kendrick-upt-7q2m9x`. Anyone who knows the name can read it.

### 2. GitHub repo
- Create a new **public** repo (free Pages needs public; private works on GitHub Pro) and push this folder to it.
- **Settings → Secrets and variables → Actions**
  - *Secrets* tab: `NTFY_TOPIC` = your topic name
  - *Variables* tab: `SITE_DOMAIN` = `jobs.peanutsandalmonds.com`, `SITE_URL` = `https://jobs.peanutsandalmonds.com`
- **Settings → Pages → Source:** choose **GitHub Actions**.
- **Actions** tab → *UPT job watch* → **Run workflow**. The first run records the current status as a baseline and sends no alerts.

### 3. Point your domain at it
At your registrar's DNS settings for peanutsandalmonds.com, add:

| Type  | Name | Value                              |
|-------|------|------------------------------------|
| CNAME | jobs | `<your-github-username>.github.io` |

Then in **Settings → Pages**, enter `jobs.peanutsandalmonds.com` as the custom domain and tick **Enforce HTTPS** once it's offered.

## Run it locally
```
python upt_watch.py --no-notify        # live fetch; writes site/index.html and data/state.json
python upt_watch.py --squadrons-html tests/squadrons_fixture.html --pages-dir tests --no-notify   # offline test
```

## If it breaks
If MilRecruiter changes its page layout, the script exits with "no squadrons found".
GitHub then emails you that the workflow failed. The parsing is in `parse_squadrons()` and `parse_squadron_jobs()`.

Each run makes about 15 page requests spaced 1.5 seconds apart, every 3 hours. Read MilRecruiter's terms of use if you plan to make the page public.
