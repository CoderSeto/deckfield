# DECKFIELD

A SQLite-backed ratings engine and dashboard for DECKFIELD, a 160-team
card-driven sports league simulator ("Baccer"), plus the game itself
(`deckfield.html`). See `CLAUDE.md` for the full technical detail
(formulas, bracket structures, known open items) — this file is just the
practical "how do I run this" guide.

## Repo layout

```
deckfield.html                    the game (match simulator) — open directly in a browser
deckfield_dashboard.html          generated dashboard snapshot — open directly in a browser
deckfield_ratings.py              the engine: schema, ratings, brackets, schedule
deckfield_cli.py                  command-line tool for updating the database
regenerate_dashboard.py           rebuilds the dashboard from the database
season.json                       which season is running (10)
seasons/s<N>/                     each season's fixed inputs: league pods, cup seedings,
                                  starting values, accolades, drawn round orders
conf_pods.json                    regional pods (the same every season)
prepare_season.py                 writes seasons/s<N>/ from the previous season's frozen record
draw_schedule.py                  draws each region's and division's round order
check_schedule.py                 independent checker for that draw
results/s10/                      every S10 matchday's results, as CSVs -- the season's record
results/s9/                       S9's results (round 12 on) -- its audit trail
hall_of_fame.json                 every finished season's Hall of Fame
legacy_points.json                Legacy Points by team and season
deckfield_dashboard_s9.html       Season 9's final dashboard, frozen (never regenerated)
archive/s9_final.json             Season 9's final numbers as plain JSON -- the S9 record
archive/s9_inputs.json            what Season 10 needed from S9 that the dashboard never showed
archive/SHA256SUMS                checksums regenerate_dashboard.py verifies before running
migrate_s9.py                     Season 9's one-time migration from the Excel workbook
Baccer Game S9 Stats.xlsm         source workbook (committed once, read-only)
CLAUDE.md                         full project memory / technical reference
SEASON10_PLAN.md                  the Season 10 turnover decisions
deckfield.db                      the database — gitignored, always regenerable
```

**The database is never committed.** It's a build artifact: fully
reproducible from the season's starting files plus every CSV in
`results/s10/`. What's actually durable and versioned is `seasons/s10/`
(written once, before the season) and the `results/s10/` folder (growing one
file per matchday). Season 9 is frozen in `archive/` and is never rebuilt
to change anything.

## First-time setup

```
pip install openpyxl playwright
python3 deckfield_cli.py start-season
```

This builds `deckfield.db` for Season 10 from `seasons/s10/`. Safe to re-run
any time — it drops and rebuilds everything rather than duplicating data.

## The day-to-day loop

1. **Find out what's next:** open the dashboard's **Next Matchday** tab and
   press **Copy Matchday Pack**, then paste it into **Import Matchday Pack**
   at the top of `deckfield.html`'s Schedule tab. (`python3 deckfield_cli.py
   next-matchday` prints the same matchups.)

2. **Play the matchday** in `deckfield.html`, then download its results: the
   file is already named for `results/s10/`, e.g. `s10-w1-weekend-r-1.csv`.

3. **Save it in `results/s10/`.** This file *is* the record — treat it the
   way you'd treat a save file, not a scratch export.

4. **Load it into the database:**
   ```
   python3 deckfield_cli.py add-results results/s10/s10-w1-weekend-r-1.csv
   ```
   This updates the real database and recomputes ratings from the
   earliest affected round forward.

5. **Regenerate the dashboard** (`python3 regenerate_dashboard.py`) and
   commit the result file and the dashboard together.

## Rebuilding from scratch

If the database is ever lost, corrupted, or you're setting up on a new
machine, the entire season is recoverable:

```
python3 deckfield_cli.py start-season
python3 deckfield_cli.py add-results results/s10/<file1>.csv
python3 deckfield_cli.py add-results results/s10/<file2>.csv
...  # every file in results/s10/, in the order they were played
```

(CLAUDE.md's runbook has the one-liner that sorts them by round.)

## Running the HTML files

Both `deckfield.html` and `deckfield_dashboard.html` are self-contained —
no server needed. Double-click, or open via `file:///path/to/file.html`.

To make them reachable from anywhere without needing local copies, enable
**GitHub Pages** on this repo (Settings → Pages → deploy from branch).
Both files become plain URLs. The dashboard still only updates when you
regenerate and push a fresh snapshot — Pages just makes it a link instead
of a local file.

## Other CLI commands

```
python3 deckfield_cli.py status              # quick summary of current DB state
python3 deckfield_cli.py recompute --from N  # recompute ratings from round N onward
```
