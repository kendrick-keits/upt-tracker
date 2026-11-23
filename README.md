# UPT Job Watcher

MilRecruiter has no public API, so this reads its public pages every 3 hours (its robots.txt allows these pages):
- **https://app.milrecruiter.com/squadrons** shows about 170 ANG and Reserve squadrons and whether each one is accepting UPT applications.
- **Each squadron accepting UPT** has a page listing its open jobs with aircraft, application deadline and a link. Rated postings are ignored.

The watcher does three things:
1. Tracks every open UPT posting by its job ID.
2. Sends a **push notification to your phone** for each new UPT posting or deadline change.
3. Publishes a **web page** of all open UPT postings, soonest deadline first. It has ANG/Reserve filters and lists postings that were recently removed. For example, it can live at 
