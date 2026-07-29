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
migrate_s9.py                     one-time migration from the Excel workbook
CLAUDE.md                         full project memory / technical reference
Baccer_Game_S9_Stats.xlsm         source workbook (committed once, read-only)
conf_pods.json, league_pods.json  pod structures for the Regional/League schedule
cup_seeds_full.json               Ribbon/Dream/Star Cup seeding
pa_cup_seeds.json                 PA Cup Draw seeding
pa_process_real_seeds_v2.json     PA Cup Process seeding (resolved)
results/                          every batch of game results ever entered, as CSVs
deckfield.db                      the database — gitignored, always regenerable
```

**The database is never committed.** It's a build artifact: fully
reproducible from the workbook plus every CSV in `results/`. What's
actually durable and versioned is the workbook (once) and the `results/`
folder (forever, growing one file per matchday).

## First-time setup

```
pip install openpyxl
python3 deckfield_cli.py migrate
```

This builds `deckfield.db` from scratch and computes ratings for every
round already in the workbook. Safe to re-run any time — it drops and
rebuilds everything rather than duplicating data.

## The day-to-day loop

1. **Find out what's next:**
   ```
   python3 deckfield_cli.py next-matchday --output next.txt
   ```
   Paste `next.txt` into `deckfield.html`'s Schedule tab. Do the same for
   the roster (`export-teams`) and weather (`region-climate`) if DECKFIELD
   needs a refresh on those too.

2. **Play the matchday** in `deckfield.html`, then export its results.

3. **Save the results as a new file in `results/`**, named for what it is,
   e.g. `results/2026-w6-tue-pa-draw-1.csv`. This file *is* the record —
   treat it the way you'd treat a save file, not a scratch export.

4. **Load it into the database:**
   ```
   python3 deckfield_cli.py add-results results/2026-w6-tue-pa-draw-1.csv
   ```
   This updates the real database and recomputes ratings from the
   earliest affected round forward.

5. **Commit the result file:**
   ```
   git add results/2026-w6-tue-pa-draw-1.csv
   git commit -m "Week 6 Tue: PA Draw round 1"
   ```

6. **Regenerate the dashboard** whenever you want a fresh published
   snapshot, and commit that too.

## Rebuilding from scratch

If the database is ever lost, corrupted, or you're setting up on a new
machine, the entire history is recoverable:

```
python3 deckfield_cli.py migrate
python3 deckfield_cli.py add-results results/<file1>.csv
python3 deckfield_cli.py add-results results/<file2>.csv
...  # every file in results/, in the order they were played
```

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
