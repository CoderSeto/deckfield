# DECKFIELD Ratings Engine — Project Memory

This file exists so a fresh Claude Code session doesn't need everything
re-explained. It captures decisions, formulas, and conventions that aren't
always obvious from the code alone.

## What this project is

A SQLite-backed ratings engine for DECKFIELD, a 160-team card-driven sports
league simulator ("Baccer"). It replaces the original Excel workbook
(`Baccer_Game_S9_Stats.xlsm`) as the source of truth for team ratings,
standings, schedules, and cup brackets. `deckfield.html` — the actual
game-playing tool, a single-file HTML/JS match simulator — now lives
alongside this engine as **the version of record** (originally built in a
separate conversation, but that copy is no longer authoritative as of
2026-07-29 — this project's copy is what gets edited and played going
forward, and the two should stay in this one place together, not
cross-referenced between conversations).

## File map

- `deckfield_ratings.py` — the engine: schema, all rating formulas, fatigue,
  S8 carryover, RLStr, Regional/League schedule generator, RDS Cup and PA
  Cup bracket generators, the Regional Tournament (postseason) generator,
  the weekly schedule / next-matchday system, region colors, and the
  DECKFIELD export functions (Teams roster, Schedule, Region Climate).
- `deckfield.html` — the game itself (match simulator). Version of record
  as of 2026-07-29; edit this copy, not one in another conversation.
- `migrate_s9.py` — one-shot migration from the real Excel workbook into
  the database. Exposes `run_full_migration(workbook_path=None)`.
- `deckfield_cli.py` — the CLI for local use without going through chat.
  Commands: `migrate [workbook.xlsm]`, `add-results <csv>`,
  `recompute --from N`, `status`, `next-matchday [--output file]`,
  `export-teams [--output file]`, `region-climate [--output file]`,
  `export-results [--from N] [--to N] [--force]`.
- `Baccer Game S9 Stats.xlsm` — the real workbook, in the repo root. Note
  the **spaces**: `migrate_s9.XLSM_PATH`'s default
  (`/mnt/user-data/uploads/Baccer_Game_S9_Stats.xlsm`, underscores) is a
  path that does not exist here, so always pass the workbook explicitly.
- `deckfield.db` — the SQLite database (gitignored; **`migrate` alone only
  rebuilds rounds 1-11** — the full rebuild is migrate + replaying
  `results/`, see the runbook below).
- `deckfield_dashboard.html` — a **static snapshot** dashboard: Rankings,
  Standings, RL Strength, Schedule, RDS Cup, PA Cup, Next Matchday,
  Calendar, Regional Playoffs. Rebuilt by re-running the export scripts
  described below; there's no `dashboard` CLI command yet — this is the
  biggest piece of unfinished plumbing.
- Static data files bundled alongside the engine (needed by the export/
  schedule functions, not regenerated per-run): `conf_pods.json`,
  `league_pods.json` (Regional/League pod structures), `cup_seeds_full.json`
  (Ribbon/Dream/Star seeding), `pa_cup_seeds.json` (PA Cup Draw seeding),
  `pa_process_real_seeds_v2.json` (PA Cup Process seeding, real and
  conflict-resolved), `rank_history_verbatim.json` (the 9 historical Rank
  History checkpoints, copied verbatim from `Rankings!CT:CL` -- see
  "Rank/Elo History tab" below).
- `CLAUDE.md` — this file.

## THE RUNBOOK: adding a matchday's results

**Read this section before doing anything else when the task is "add these
results."** Everything here is also explained in depth further down, but
the details are spread across a dozen sections and the steps below are the
whole job in order. Do not skip the verification steps — they are how every
bug recorded in this file was caught.

### 0. Bootstrap the container (fresh session = nothing is installed)

`deckfield.db` is gitignored and the container starts clean, so a new
session has **no database and no Python deps**:

```
pip install openpyxl playwright        # neither is preinstalled
```

Do **not** run `playwright install` — it is blocked, and Chromium is
already on disk. See step 6 for how to point Playwright at it.

### 1. Rebuild the database (migrate, then replay `results/` IN ROUND ORDER)

```
python3 deckfield_cli.py migrate "Baccer Game S9 Stats.xlsm"
```

The workbook path is a **positional** argument, not `--workbook`, and the
filename has spaces (quote it). Migrate covers rounds 1-11 only; every
round from 12 on lives in `results/` and must be replayed:

```
for f in $(for f in results/*.csv; do r=$(sed -n 2p "$f" | cut -d, -f1); \
    echo "$r $f"; done | sort -n | cut -d' ' -f2); do
  python3 deckfield_cli.py add-results "$f"
done
```

That sorts by the round number **inside each file**, which is the only
correct order. `for f in results/*.csv` looks equivalent and is **wrong** —
a shell glob sorts `thu` before `tue`, feeding round 25 before 24, 13
before 12, and so on. (Fatigue is rederived on every ingest now, so
out-of-order replay no longer corrupts it — see the fatigue section — but
ingest in round order anyway; nothing else guarantees it stays that way.)

### 2. Prove the rebuild is faithful BEFORE ingesting anything new

```
python3 -c "import deckfield_ratings as db; c=db.get_connection(); \
  print(c.execute('select count(*) from games').fetchone()[0])"
```

Check it against the counts recorded in the recovery section above (1544
through round 26, 1576 through round 27; add the new count when you add a
round). If it does not match, **stop** — something is wrong with the
rebuild, and ingesting on top of a bad base silently corrupts everything
downstream.

Stronger check, worth doing whenever the count is the only evidence: copy
`deckfield_dashboard.html` aside, run `python3 regenerate_dashboard.py`,
and diff the `const` blocks. Every one should be byte-identical except
**`TEAMS_EXPORT_TSV`**, whose Secondary Type column is a fresh
`random.randint(1, 18)` on every export by design. That one exception is
expected; any other difference is a real problem.

### 3. Confirm the round number the CSV claims

```
python3 deckfield_cli.py next-matchday
```

It prints the event and "Use round=N when reporting results back." That N
must equal the `round` column in the CSV you were given. If it does not,
do not "fix" the CSV or hand-pick a number — work out why they disagree
first (`abs_round_for_event()` is the single source of truth).

### 4. Save the CSV into `results/` under the engine's own filename

Never invent a filename. Copy the CSV in with your best guess, then let
the engine confirm it:

```
cp <uploaded.csv> results/<year>-w<week>-<day>-<event>.csv
python3 deckfield_cli.py add-results results/<...>.csv
python3 deckfield_cli.py export-results --from N --to N
```

`export-results` derives the filename from
`full_schedule_abs_round_mapping()` + `WEEKLY_SCHEDULE`. If your name was
right it prints "already matches" and writes nothing — which also proves
the ingest round-trips losslessly. If your name was wrong it writes the
correct file, and you delete yours.

**Keep the CSV. Always.** The database is gitignored precisely because
`results/` is supposed to be able to rebuild it; that only holds if every
ingested matchday's CSV is committed. Eleven rounds were once lost exactly
this way (see the recovery section).

### 5. Regenerate the dashboard — always, no exceptions

```
python3 regenerate_dashboard.py
```

Per explicit standing instruction, results are never added without this.
It rebuilds every derived constant plus the header subtitle. If it raises
about the const manifest, read `_check_const_manifest()`'s message and
decide whether the new constant is derived or static — do **not** silence
it by adding the name to `STATIC_CONSTS` without checking what it is
derived from. That guard exists because this exact bug shipped six times.

### 6. Verify in a real browser before committing

Executing the `<script>` block in Node with a mocked `document` is the
documented minimum, but Playwright against the real file is strictly
better and is what the recent fixes used:

```python
from playwright.sync_api import sync_playwright
import glob
CHROME = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")[0]
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=CHROME)   # REQUIRED
    ...
```

`executable_path` is required: pip installs a newer Playwright than the
Chromium build on disk, so a bare `launch()` fails looking for a build
number that was never downloaded.

Click through every tab (`button[data-tab="..."]`; the RDS one is `rds`,
not `rdscup`; panels are `#panel-<tab>`) and assert **zero `pageerror`
events**. Two things are expected noise and are **not** code bugs:

- A failed request to `fonts.googleapis.com` (`ERR_CONNECTION_RESET`) —
  the sandbox blocks outbound network. It surfaces as a console error and
  a `requestfailed`, never as a `pageerror`. Distinguish the two before
  reporting a failure.
- Any check you wrote that greps rendered text for a section label. Query
  the DOM (`.sb-round`, `thead th`) instead; text-matching produced two
  false failures on a page that was rendering correctly.

### 7. Commit and push

Commit `results/*.csv`, `deckfield_dashboard.html`, and anything else you
changed. Never commit `deckfield.db` (it is gitignored — keep it that
way). Push to the branch named in the session instructions.


## The rounds 15-25 recovery (2026-09-03) — resolved

For a while `results/` held only rounds 12-14 while the dashboard was at
round 25. Rounds 15-25 had each been ingested through a PR (#34-#48) that
committed **only** the regenerated `deckfield_dashboard.html` — the source
CSVs were never saved, so those eleven matchdays existed solely in one
session's container database. Confirmed by searching every blob in every
commit across all branches plus dangling objects: zero CSV rows for those
rounds were ever in git.

They were recovered from that session and are now committed. Verified from
a clean wipe-and-rebuild (workbook + all 14 CSVs):

- **1464 games through round 25**, matching the count PR #48's own test
  plan recorded weeks earlier — an independent check written before the
  recovery existed.
- Rebuilding `DATA` reproduces the previously-committed dashboard
  **exactly**: 160 teams identical across all 36 fields (`ovr`, `rank`,
  `elo_raw`, `tot`, `dscr`, `eye`, `rlstr`, `sos`, `sov`, and each team's
  full per-game `log`).
- Regenerating the whole dashboard afterwards left **12 of 14** derived
  constants byte-identical (`DATA`, `SCHEDULE_DATA`, `RANK_ELO_HISTORY`,
  `CUP_REAL_RESULTS`, `RDS_ROUND_PAIRINGS`, `PA_*`, `STRENGTH_DATA`,
  `CALENDAR_DATA`, `NEXT_MATCHDAY_DATA`). Only `RT_DATA` changed (the
  staleness fix, 39/40 MD1 slots corrected) and `TEAMS_EXPORT_TSV` (whose
  Secondary Type column is a fresh `random.randint(1, 18)` every export by
  design).

Round 26 (L7) was absent at the time of the recovery because it had not
been played; it was ingested on 2026-09-03 and `results/` now runs 12-26
(15 files). A clean rebuild reports 1544 games through round 26.

Round 27 (PA Cup Draw round 3, week 11 Tue) was ingested 2026-09-08 as
`results/2026-w11-tue-pa-draw-3.csv`; `results/` now runs 12-27 (16 files)
and a clean rebuild reports 1576 games through round 27. The rebuild used
to ingest it was itself verified against this file's own record: migrating
the workbook and replaying rounds 12-26 in round order reproduced **1544
games** and a dashboard whose constants were byte-identical to the
committed ones, with the single documented exception of
`TEAMS_EXPORT_TSV` (fresh `random.randint(1, 18)` Secondary Type every
export, by design). `export-results --from 27 --to 27` then reported the
new file already matching byte for byte, so the round trip is lossless and
the filename matches the engine's own derived convention.

Rounds 28-31 (PA Process round 3, R8, L8, L9) were added on main by a
separate session while the Qualification tab was being built on a branch,
so the branch had to pick them up before merging. `results/` now runs
12-31 (20 files) and a clean rebuild reports **1848 games through round
31**. Verified 2026-09-10 the same way as every rebuild above: rebuilding
from the workbook plus all 20 CSVs and regenerating reproduced main's
committed dashboard with every derived constant byte-identical, the only
differences being `TEAMS_EXPORT_TSV` (random Secondary Type, by design)
and the newly added `QUALIFICATION_DATA`. That reproduction is what
proved the merge lost nothing -- worth repeating whenever a branch has to
absorb rounds added elsewhere.

## Recovering rounds that only exist in the database

`deckfield export-results` is the exact inverse of `add-results`: it writes
games back out in the same CSV format, so a round that was ingested without
its CSV being kept can be recovered into `results/` and the database goes
back to being a rebuildable artifact instead of the only copy of the data.

```
python3 deckfield_cli.py export-results                 # everything results/ owns
python3 deckfield_cli.py export-results --from 15 --to 25
```

- **Default scope is the first post-workbook round onward** (round 12
  today, from `historical_last_round()` = `max(_CONFIRMED_ABS_ROUND)`).
  This matters: rounds 1-11 come from the workbook via `migrate`, so
  putting them in `results/` too would double-insert every one of those
  games on the next rebuild. Asking for them explicitly still works but
  prints a warning.
- **Filenames match the existing convention** (`2026-w6-tue-pa-draw-1.csv`),
  derived from `full_schedule_abs_round_mapping()` + `WEEKLY_SCHEDULE`, so
  recovered rounds sit alongside hand-kept ones with no naming drift.
- **Output is byte-identical to what `deckfield.html`'s Download CSV
  produces** — same per-column decimal places (raw_goal 2dp, spread 4dp,
  interest 2dp, dscr 1dp, elo/ex integer) and no trailing newline. So
  re-running it over an up-to-date `results/` is a true no-op rather than a
  wall of formatting churn, and an existing file that *differs* from the
  database is refused unless `--force` is passed.
- **The round trip is lossless.** `games` stores every CSV column verbatim
  and nothing is derived on ingest. The one field not stored directly is
  `home`, recovered from `host_region`: "A" when it matches team_a's
  region, else "B" — exact for all 928 real games (a cross-region game
  matches exactly one side; a Regional game's two teams share a region, so
  either answer resolves to the same `host_region`, which is all `home` is
  used for).

Verified end to end: exporting rounds 12-14 reproduced the three
already-committed CSVs **byte for byte**, and a full wipe-and-rebuild from
the workbook plus the *exported* CSVs produced a database identical to one
built from the *committed* CSVs — all 480 rating rows across 28 columns,
all 928 games across 21 fields, and all 1,856 fatigue deltas.

**Standing rule: keep the CSV for every matchday you ingest.** The
database is gitignored precisely because it's supposed to be reproducible;
that only holds if `results/` is complete. Every round from 12 on now has
its CSV here, and a rebuild reproduces the dashboard exactly — keep it that
way.

### Fatigue deltas are rederived on every ingest (fixed 2026-09-03)

`add-results` now calls `recompute_all_fatigue_deltas()` after ingesting a
file, before recomputing ratings. Without it, **replaying `results/` in the
wrong order silently corrupts fatigue** — and the natural way to replay it
is wrong: a shell glob sorts `thu` before `tue`, so `for f in
results/*.csv` feeds round 25 before 24, 13 before 12, 16 before 15, 19
before 18, and 22 before 21.

The mechanism: `_record_fatigue_for_team()` asks "what was this team's most
recent game *before* round N", which is only correct if every earlier round
is already in the database. When round N-1 is still missing it finds an
older game instead, computes too large a `skipped`, and halves that round's
delta (`fatigue = distance / 2^skipped`). Caught during the rounds 15-25
recovery: 307 delta rows differed, concentrated in exactly those six
out-of-order rounds, and fatigue was wrong for 159/160 teams while all 35
other rating fields matched perfectly.

Rederiving from `host_region` history makes ingest order irrelevant, and
matches how fatigue is defined everywhere else in this file (recomputed
from real host history, never trusted as stored). Verified: ingesting all
14 files in glob order, strict round order, and full reverse round order
now produces byte-identical fatigue deltas *and* ratings, and the reverse
-order build still reproduces the dashboard exactly. This mattered beyond
bookkeeping — fatigue feeds `export_teams_for_deckfield()`, which feeds
`deckfield.html`'s Spread calculation.

## Adding new game results

*(Format reference. For the end-to-end procedure — rebuilding the database
first, verifying it, naming the file, regenerating and checking the
dashboard — follow **THE RUNBOOK** near the top of this file.)*

**Always use the CSV format**, not the old packed-string format
(`3R!113+36-33^4.13(0.93)[102]{42.7}2183`). One row = one full game, both
teams at once:

```
round,game_type,team_a,team_b,result_a,pf_a,pa_a,raw_goal_a,raw_goal_b,spread_a,interest,dscr_a,dscr_b,elo_a_after,elo_b_after,home,ex_a,ex_b,cup_name,cup_bracket,cup_round
```

- `team_a`/`team_b` are `team_id` (dex) integers, not names.
- `result_a`: points team_a earned — 3 win, 2 OT win, 1 OT loss, 0 loss. No draws exist.
- `home`: `"A"` or `"B"` — resolves `host_region` automatically, which drives fatigue tracking.
- `ex_a`/`ex_b`: optional, default 0. As of 2026-07-30, both teams bank their
  own independently-computed EX Bonus every game (win or lose) — this is a
  deliberate departure from the historical S9 data, where `migrate_ex_bonus()`
  only ever awarded a bonus to the winner, and as the *differential*
  (`winnerEX.ex - loserEX.ex`), never a raw per-team score. Going forward,
  `deckfield.html`'s Results tab exports each team's own raw EX score
  independently for both `ex_a` and `ex_b`.
- `cup_name`/`cup_bracket`/`cup_round`: optional, but **required for any cup
  game** (`Ribbon`/`Dream`/`Star`/`PA`, `Draw`/`Process`, integer round) — the
  weekly-schedule completion check (`next_matchday`) can't tell a cup game
  was played without these, and would loop on the same event forever.

Ingest via `deckfield add-results results.csv` (recomputes ratings
automatically from the earliest affected round) or `add_game_from_dict()` /
`add_game_csv()` in Python directly.

**`migrate` is safe to re-run** — `init_db()` explicitly drops all tables
before recreating them. This was a real bug once: `CREATE TABLE IF NOT
EXISTS` doesn't touch existing data, so re-running migrate on an existing
database silently duplicated every row (caught because Canalave's record
suddenly read "10-0" instead of "5-0"). Don't remove the DROP statements.

## Schema essentials

- `games`: one row per game, `team_a`/`team_b` perspective. **Team B's
  stats are never stored separately** — `pf_b == pa_a`, `pa_b == pf_a`,
  `result_b == 3 - result_a`. This is deliberate (see `_team_game_rows`):
  a single source of truth instead of double entry, which is exactly the
  bug class the old Excel format was prone to.
- `game_type`: `R` (Regional), `L` (League), `S` (Cup/Swiss), `P`
  (Playoffs — includes the Regional Tournament), `F` (Finals).
- `cup_name`/`cup_bracket`/`cup_round` on `games`: populated for RDS Cup
  games matched against real Archive data, and for any cup game entered
  going forward via the CSV's optional fields. Everything else with
  `game_type='S'` and no cup tag is an untagged cup/swiss game.
- `host_region`: backfilled for all historical S9 games from the Archive
  tab's Home/Away column; set automatically for new games via the CSV's
  `home` field.
- `fatigue_deltas`: per-round travel fatigue, **recomputed from real
  host_region history**, not trusted from the original sheet — the sheet's
  own fatigue numbers didn't match its own Home/Away data on cross-check.
  `team_seasons.starting_fatigue` is the one value still sourced directly
  from the sheet (column AJX position 1 in Calcs).

## The weekly schedule & "what's next" system

`WEEKLY_SCHEDULE` (in the engine) is the full go-forward calendar — 26
weeks, each with Tue/Thu/Weekend slots — replacing the historical
Excel-run order for everything from here on. Event kinds: `("R", n)`,
`("L", n)` (Regional/League, own 1-15 sequence), `("RDS", bracket,
cup_round)`, `("PA", bracket, cup_round)`, `("RT", matchday)` (Regional
Tournament, matchday 1-9). Weeks 24-26 are the Regional Tournament
(RT1-RT9, one per Tue/Thu/Weekend slot).

**Week 6 is a permanent one-time special case**: it replaces the original
weeks 5 and 6 (PA Draw/Process round 1 + R4, then L2/L3/R5) with `{Tue:
PA-D-1st, Thu: PA-P-1st, Weekend: L3}`, because R4, L2, and R5 were
already played (in the old Excel-run order) before this reconciliation was
needed. Applied once in `WEEKLY_SCHEDULE` directly; don't try to derive it
from a formula, and don't expect this pattern to recur.

**Absolute round numbers**: `abs_round_for_event()` /
`full_schedule_abs_round_mapping()` walk `WEEKLY_SCHEDULE` in order,
using the confirmed historical values (`_CONFIRMED_ABS_ROUND`, validated
against real Archive data for rounds 1-11) where they exist, and assigning
the next sequential integer to everything else. This is the single source
of truth for "what absolute round number goes in `games.round`" — always
call `abs_round_for_event()` rather than hand-picking a number. Two-legged
ties and best-of-three slots (RDS SF/Final, PA QF/SF/Final — `cup_round is
None`) need week+day folded into their lookup key, since the same
`(kind, bracket, None)` repeats across Tue/Thu/Weekend for each leg —
handled in `_schedule_event_key`, but worth remembering if extending this.

**`next_matchday(season)`** walks the schedule and returns the first slot
without real data — this is genuinely determined by checking the database,
not a fixed pointer, so it advances correctly as real results come in.
**`export_matchday_for_deckfield()`** returns the matchday's games in
DECKFIELD's Schedule paste format, plus `abs_round` — the exact value to
use when logging that matchday's results back via the CSV format above.
Games are sorted by each game's **worst-ranked (highest rank number)
team, descending** — rank #160 plays first, then #159, and so on — since
that's the actual play order, not `_games_for_event()`'s native
bracket/pod order (e.g. PA Cup's ladder-row order, which is the right
order for the PA Cup tab itself but not for this export). Real bug, fixed
2026-08-04: the dashboard's Next Matchday tab was pasting `_games_for_event()`'s
raw order straight through, so PA Cup matchdays came out looking like a
copy of the PA Cup tab instead of play order.

**What's actually reachable** via `_games_for_event()`: Regional/League any
round (via the pod schedule), RDS Cup rounds 1-5 (resolving through real
prior-round winners where needed — round 3+ isn't a placeholder, it's
generated live), PA Cup rounds 1-8 (same), and RDS's mutual semifinal/final
(wired 2026-09-11). **PA's mutual semifinal/final** raise
`NotImplementedError` deliberately — not modeled yet, see "Known open
items."

## Ratings formulas (validated against real S9 data)

- **RW**: exact match, 160/160 teams, against real S9 data. **LW's 3+ games
  branch has since been deliberately corrected away from that historical
  match** (see below) — RW's formula is untouched and still exact. Full
  formula in `deckfield_ranking_spec.md`-equivalent docstrings in the
  engine.
- **OVR** = weighted blend of 10 components (PF, PA, PDG, SOS, SOV, DSCR,
  EYE, Elo, Cups, EX), each normalized 0–100. PDG's denominator is
  `avg_raw_goal_score` (the `^X.XX` field in the packed string), **not**
  PF/games_played — this was a real early bug.
- **Block A uses RW/LW, not PF/PA, fixed 2026-08-07 (per explicit
  instruction).** `compute_round_ratings()`'s OVR assembly previously had
  Block A = `(PF_norm×.5 + PA_norm×.5) × 3` — matching
  `deckfield_ranking_spec.md`'s §6 exactly, but the person flagged this as
  wrong: **Block A should be `(RW×.5 + LW×.5) × 3`**, using RW/LW *raw* (not
  population-normalized — they're used everywhere else in the system in
  their raw, uncapped form; there's no "RW_norm"/"LW_norm" concept anywhere
  in the spec or engine). Block B is unaffected — it still uses
  `PF_norm`/`PA_norm` alongside PDG, so PF/PA didn't disappear from OVR
  entirely, they just moved out of Block A. This is a real formula
  correction, not a bug fix to match some other source of truth — the old
  Block A (PF/PA) was byte-identical to the original spec, but the spec
  itself is being corrected here. Recomputed all of season 9 (`recompute_from_round(9,
  1)`, rounds 1–14) since OVR feeds SOS round-to-round.
- **LW's 3+ games branch: additive division bonus, not a multiplier on the
  whole base — fixed 2026-08-07, same day, per explicit correction.** The
  original spec's `LW = LW_base × (1 + 0.05×(11−league_division))` (§4,
  previously validated exact against real S9 data, 160/160 teams) badly
  overweighted low-numbered divisions even at a small early-season sample
  — division 1 gets a full ×1.5 multiplier on the *entire* base, not just
  the division-strength component of it. Corrected to
  `LW = LW_base + 0.05×(11−league_division)×league_wins` — the division
  bonus is now a small additive nudge scaled by how many league wins
  actually back it up, not a blanket multiplier on everything (including
  the SP/playoff terms already folded into `LW_base`). Canalave City at
  round 14 (division 1, `LW_base` 102.4, 3 league wins):
  ```
  LW_base = (LP/(league_games×3+regional_games) + SP/1000 + pW×0.005) × 100
          = (14/(3×3+5) + 24/1000 + 0) × 100 = (14/14 + 0.024) × 100 = 102.4
  LW      = LW_base + 0.05×(11−league_division)×league_wins
          = 102.4 + 0.05×(11−1)×3 = 102.4 + 1.5 = 103.9
  ```
  (Previously: `LW_base × 1.5 = 153.6` — the old multiplier form, now
  removed.) With this and the Block A fix together, recomputing all of
  season 9 brought Canalave City's round-14 OVR to 105.72 (RW 102.4, LW
  103.9 now much closer together, as expected for a team unbeaten in both
  tracks) and the round-14 LW range to 0–103.9 (avg 51.7, comparable
  scale to RW), down from the intermediate 0–153.6 the multiplier form
  produced. OVR range 9.77–105.72 (avg 54.7), still sane, no NaN/negative
  blowups. This is the same kind of deliberate correction as the Block A
  fix above, not a bug fix chasing the original spreadsheet's byte-exact
  output — the multiplier form's 160/160 historical match no longer holds
  by design.
- **EYE's `X` term uses `avg_pd`, not raw season-total `PD`, fixed
  2026-08-07, same day, per explicit correction.** Found during a cursory
  correctness pass prompted by the two fixes above: the spec's EYE formula
  explicitly labels its `PD` term "raw, season total" (unlike PF_norm,
  PA_norm, PDG, and SOV's `vic_avg`, which all already use per-game
  averages — `avg_pf`/`avg_pa`/`avg_pd` — for their PD-flavored inputs),
  and the engine matched that literally: `x = (v["pd"] - min_pd) *
  (v["avg_gi"] / 100)`. That's an inconsistency worth taking seriously:
  raw `PD` grows with games played, so a team that's played more real
  games (vs. one that's had a cup bye) gets a mechanically larger `PD` at
  *identical* per-game performance, inflating `X` → `Y` → `AD` → `Z` →
  `EYE` for no real reason. Confirmed this isn't a hypothetical — at round
  14, `games_played` genuinely ranges 10–14 across the 160 teams (cup
  byes: Dream/Star's 16 bye seeds in each of RDS Draw/Process rounds 1-2),
  so this was a real, live skew, not a no-op. Corrected `x = (v["avg_pd"] -
  min_avg_pd) * (v["avg_gi"] / 100)`, reusing the same `avg_pd`/
  `min_avg_pd` PDG already computes — the now-unused raw `pd`/`pd_vals`/
  `min_pd` were removed entirely (nothing else referenced them). `Y`,
  `AD`, `Z`, and `EYE` all derive from `X`, so they inherit the fix
  automatically; `avg_GI` and `RLStr` don't need any change (`avg_GI` is
  already a per-game average by definition, `RLStr` isn't a per-team
  accumulation at all). Recomputed all of season 9. Zapapico at round 14
  (a mid-pack team used as the running example): EYE 44.67 → 51.76,
  `games_played` 12 (right in the middle of the 10-14 range, so a
  representative, not extreme, case). Round-14 EYE range is 0–100 (avg
  49.2, both ends still reachable), OVR range 9.78–105.76 (avg 55.1) —
  barely moved from the Block A/LW fixes' 9.77–105.72, since EYE is only
  one of several Block D inputs and the skew only bites for teams whose
  games_played differs from the round's mode.
- **OVR's EX taper** (`taper_n`, `N/14` in Block E and the overall
  denominator) — changed from an original `N/12` scheme. Full weight (14)
  through week 6, decreasing 1/week starting week 7, reaching 1 at week 19,
  0 starting week 20. Confirmed this changes nothing retroactively: week 6
  is the latest week with real data (round 11), where 14/14 and 12/12 are
  both exactly 1 — Canalave's OVR came out byte-identical (101.76, Grade
  SS) before and after the change.
- **TOT floor**: `TOT = max(0, RP + LP + SP + P + F + B + STARTING_TOT)`
  (`_points_buckets()`). `STARTING_TOT = 20` was always meant as a floor
  against going negative, but that only held by construction while `B` (EX
  Bonus) was winner-only and positive-biased. Now that both teams bank
  their own EX Bonus every game — including negative components on a loss
  — a bad enough streak could push the raw sum below zero without an
  explicit clamp. Confirmed a no-op against real S9 data: minimum `TOT`
  across all 1,920 historical `team_round_ratings` rows is exactly 20.0,
  never lower, so nothing changes retroactively.
- **RLStr** (Regional/League Strength): a **from-scratch revamp**, not a
  faithful port of the sheet's original formula (which the person
  confirmed had manual entry errors). Current version: 7 z-scored
  components per region/division (Rank ×2 flipped, OVR ×2, Wins ×1, DSCR
  ×2, Elo ×1, TOT ×1, GI ×1 — GI is a plain average, deliberately
  non-circular), summed, then passed through
  `1.0 + 0.75 * tanh(weighted_sum / (2 * population_stdev))`. No
  iteration/fixed-point convergence needed — earlier circular versions did,
  this one doesn't.
- **Fatigue**: `fatigue(prev_region, curr_region, skipped_matchdays) = REGION_DISTANCE_MATRIX[a][b] / 2^skipped`.
  Cumulative fatigue = `starting_fatigue + sum(deltas)`. Season transition:
  `fatigue_seed = ceil(prev_season_final_fatigue / 5)` — round **up**, confirmed explicitly.
- **S8 Carryover**: `ovr_seed = (prev_ovr - 50) * 0.75 + 50`; PF/PA seeds
  use the **normalized 0–100 OVR components** (`pf_norm`/`pa_norm`), not
  raw season point totals — this was a real bug, caught and fixed. DSCR
  seed uses the raw 0–10 D-Sqrt value, not the normalized component. Blend
  window is matchdays 1–8 (`blend_stat`/`blend_weights`).
- **STARTING_ELO = 1800.0** — confirmed value, matters only for a team's
  very first game ever (relevant if new teams are ever added).

## Regional/League pod schedule

16 teams per region/division split into 4 pods of 4 (2×2: UL/UR/LL/LR),
each pod's 4 teams also position-ordered 1–4 from the Conf/League tabs.
15-round full round robin:

- **Pod rounds** (1, 8, 15): within-pod matchups, explicit position pairs.
- **Across/Same/Diag rounds** (4 rounds each, appearing twice across the
  cycle): pod-pairs use a primary position pattern (e.g. `1v3/2v4`) plus
  an automatic **complementary pairing** (mirror each pair: `1v3` also
  implies `3v1`) to cover all 4×4 cross-games across the 4 rounds. "At X"
  fixes which side hosts for both primary and complementary games.

Verified exactly against real Archive data (pairings *and* host side) for
multiple round types before trusting the algorithm.

## RDS Cup (Ribbon/Dream/Star)

64-seed Draw-and-Process croquet-style knockout. Ribbon = Indigo/Silver/
Delta/Lily Valley (64 real teams, no byes). Dream = Vertress/Phoenix/
Lanakila, Star = Kalosite/Dynamax/Terastal (48 real teams each, seeds
49–64 are byes in **both** Draw and Process).

- **Draw**: standard bracket, seed *K* vs seed *(65−K)* round 1. Must use
  proper recursive seed-placement order (`_draw_round1_pairs`), **not**
  naive ascending `K, 65-K` order — the pairs are identical either way,
  but only the recursive order correctly encodes who meets whom in round
  2+. This was a real bug, found by cross-checking real round-2 results.
  Higher (better) seed hosts.
- **Process**: fixed round-1 seeding list (`PROCESS_ROUND1_PAIRS`) — every
  seed 1–16 pairs with a seed 49–64. Lower (worse) seed hosts.
- Both validated **exactly** against all 128 real round-1 games and all 96
  real round-2 games (one game, `Hau'oli City`, has a confirmed data-entry
  error in the source sheet — a team appears to lose round 1 but plays
  again in round 2. Accepted as a known sheet error, not modeled around).
- **Rounds 3-5 are fully generated**, not placeholders — `_rds_round_games()`
  resolves through real prior-round winners for any target round, raising a
  clear error (not a guess) if a prior round isn't complete yet.
  **The Hau'oli City anomaly above became a real blocker once round 3
  generation was actually attempted for the first time, fixed 2026-08-07**:
  when rounds 1-2 were originally validated, "not modeled around" just meant
  the discrepancy was noted and left alone, since nothing downstream
  actually depended on round 2's exact opponent identity yet. The first
  real attempt at Dream Draw round 3 (round 14's `add-results` advancing
  `next_matchday()` past PA/RDS round 2) hit a hard `ValueError` — Dream
  Draw's Exeggutor Island slot predicted round 2 as "vs Mistralton City"
  (derived from real round-1 winners), but the real recorded round-2 game
  was "vs Hau'oli City" (Exeggutor Island won outright, no ambiguity about
  who advances). `_real_bracket_winner()`'s exact-name-match lookup simply
  couldn't find "Exeggutor Island vs Mistralton City" and returned `None`,
  which `_rds_round_games()` correctly treated as "not complete yet" —
  correct behavior for a genuinely unplayed game, wrong conclusion for a
  played game under a different opponent. Fixed with a new
  `_rds_real_winner_with_fallback()`, used only by `_rds_round_games()`:
  tries the exact predicted pairing first, and only if that's not found,
  falls back to whichever real game either predicted participant actually
  played that round. Deliberately **not** folded into `_real_bracket_winner()`
  itself, which PA Cup's conflict-resolution system also calls — that
  system already generates and looks up exact real pairings by
  construction (see PA Cup section below), so a fallback there would only
  ever mask a genuine bug, not a historical data quirk. Verified: all 6
  RDS brackets (Ribbon/Dream/Star × Draw/Process) now resolve round 3
  cleanly, Dream Draw's round 3 correctly includes Exeggutor Island
  (paired against Pyrite Town), and every other bracket's round-3 output
  is byte-identical to before this fix (only the one known-anomalous slot
  ever takes the fallback path).
- After **5 rounds** each, Draw and Process reach 2 remaining teams, which
  combine into a shared final stage:
  - 4 distinct finalists: Draw pair plays a semifinal, Process pair plays
    a semifinal, winners meet in the final.
  - 3 distinct (1 team reached the final 2 on both sides): that team byes
    to the final; the other two play the lone semifinal.
  - 2 distinct (same pair both sides): no semifinal, straight to final.
  - Home team for the semifinal/final: whichever team has the better
    original seed (a separate rule from either bracket's own convention).
  - **Built and wired 2026-09-11**, when both brackets finished round 5
    for real and the dashboard had nothing to show. `rds_mutual_stage()`
    feeds `resolve_mutual_stage()` the real round-5 winners plus the cup's
    shared seeding; `rds_mutual_games()` turns the result into playable
    games; `_games_for_event` resolves `("RDS","SF",None)` and
    `("RDS","Final",None)` instead of raising. PA's mutual
    semifinal/final is still unmodelled.

    **Both stages are two-legged**, occupying a week's Tue and Thu slots
    (`agg = bracket in ("SF","Final")` was already true in
    `export_matchday_batches`). The written rule names only one home team
    ("whichever has the better original seed"), which describes home
    *advantage*, not a single venue -- per explicit instruction the legs
    split the Regional Tournament's way: **worse seed hosts leg 1, better
    seed hosts leg 2**. `resolve_mutual_stage()` returns the better seed at
    home, i.e. leg-2 orientation, and `_rds_leg_orientation()` flips it for
    leg 1.

    **Stored as `cup_bracket` `SF`/`Final` with `cup_round` carrying the
    LEG number (1 or 2)**, not a bracket round -- which is what lets each
    leg be checked, exported and ingested independently. `deckfield.html`'s
    Cup Bracket dropdown gained `SF`/`Final` to match; without those it
    could not have recorded these games at all.

    An exact aggregate tie goes to the **better seed** -- the same seed
    authority that decides hosting, rather than whichever team happens to
    be stored as `team_a` (which is what `_rt_tie_winner` does for the
    Regional Tournament, and is arbitrary).

    Real data exercises two of the three shapes: Ribbon and Star each have
    a team that won BOTH brackets (Canalave City, Casseroya Lake) and byes
    to the final while the other two play the lone semifinal; Dream has 4
    distinct finalists and two semifinals. So week 15 is **4 ties, 8
    games** across the two legs, not 6.
- **Dashboard bug, fixed 2026-08-04**: the RDS Cup tab's Round 2 section
  (`rdsRound2RowHtml` in `deckfield_dashboard.html`) was rendering the
  *winner* into the home column and the loser into away, unconditionally —
  it had no seed data at all and no notion of who actually hosted. Real
  host is determined by the seed rule above (Draw: lower-numbered/better
  seed hosts; Process: higher-numbered/worse seed hosts), independently of
  who won, and was cross-checked directly against the Archive tab's
  winner-home/away column (`G` = 'H'/'A' for whether the row's winner
  hosted) for several Round 1 and Round 2 games to confirm the rule holds
  for real games, not just in theory. Round 1 and Round 3 were already
  correct (seed-based, from `CUP_BRACKET_DATA`/`RDS_ROUND3`) — only Round 2
  was wrong, because it rendered straight from `CUP_REAL_RESULTS` (winner/
  loser only, no seed) instead of a seed-aware structure. Fixed by adding
  a `RDS_ROUND2` constant (home/away/seed per game, computed the same way
  as `RDS_ROUND3`) and reusing `rdsRound1RowHtml` for Round 2's rows
  instead of the now-deleted `rdsRound2RowHtml`.

## PA Cup (160 teams)

A **ladder** structure, not a shared nested bracket — this took several
wrong turns to land on, worth preserving the final model precisely. 32
independent ladder rows, row *i* = seeds `(161-i, 96+i, 97-i, 32+i, 33-i)`
for *i* = 1..32:

- Round 1: `(161-i) vs (96+i)` — both always tier D (seeds 97–160, no
  bye). This is proper seed-vs-mirror-seed pairing within the full 64-seed
  tier-D range (e.g. row 1 is 160 vs 97, row 32 is 129 vs 128) — the same
  kind of recursive pairing RDS Cup's Draw round 1 already uses, just
  applied to this bracket's own 64-seed sub-range.
- Round 2 winner faces `97-i` (tier C, seeds 65–96, bye to round 2 —
  mirrored within tier C's own range, same idea as round 1).
- Round 3 winner faces `32+i` (tier B, seeds 33–64, bye to round 3 —
  **not** mirrored, unlike rounds 1/2/4).
- Round 4 winner faces `33-i` (tier A, seeds 1–32, bye to round 4 —
  mirrored within tier A).
- Rounds 5–7: standard bracket among the 32 row-champions, keyed on each
  row's *final* seed identity (`33-i`, the same value used for round 4,
  not `i`) — row-seed *k* vs row-seed *33-k* in round 5, standard
  progression after. This rule itself never changed; it just inherits the
  corrected row identity.
- Rounds 5-8 are that bracket: 32 → 16 → 8 → 4 → 2, so **each bracket
  sends two teams into a shared mutual semifinal**, then the final — the
  same duplicate-handling rules as RDS Cup, and the same shape as RDS's
  own end stage.
- **There is no mutual quarterfinal — changed 2026-09-12 per explicit
  instruction.** The plan used to stop each bracket at round 7 (4 left)
  and open the shared stage with a mutual *quarterfinal* that paired Draw's
  four against each other and Process's four against each other. That
  pairing is a within-bracket round in everything but name, so it is now
  simply **round 8**, and the shared stage starts at the semifinal.
  Nothing had been implemented (the stage was a `WEEKLY_SCHEDULE` slot,
  a few `bracket in (...)` guards and two `NotImplementedError`s) and no
  PA game past round 3 has been played, so there was no data to migrate.
  `PA_BRACKET_LAST_ROUND = 8` is the constant to read, beside
  `RDS_BRACKET_LAST_ROUND = 5`.
  **Week 21 changed with it**: `{Tue: ("PA","QF",None), Thu: ("PA","QF",None)}`
  became `{Tue: ("PA","Draw",8), Thu: ("PA","Process",8)}` — the Draw-Tue/
  Process-Thu pattern every other PA round already uses. The slot *count*
  is unchanged, so **no absolute round number downstream shifted**
  (verified: SF still 60/61, Final still 63). One incidental improvement:
  the calendar used to print both week-21 legs as abs_round 57, since
  `abs_round_for_event` without week/day can only return leg 1's; two
  ordinary rounds get 57 and 58 correctly.
- **Rounds 2-8 are fully generated** via `_pa_round_games()`, same
  real-winner resolution pattern as RDS. Rounds 5-8 are now **one halving
  loop** rather than a block per round — the same maintenance trap the RDS
  renderer was rewritten to avoid; adding round 8 needed no new branch.
  Verified by a synthetic walk on a scratch database: rounds 1-8 produce
  32/32/32/32/16/8/4/2 games per bracket, round 8 yields exactly the two
  mutual-semifinal entrants per bracket, and round 9 raises.
  **Not yet built**: the mutual semifinal/final (raises
  `NotImplementedError`).
- **Rounds 2-4 conflict resolution and seed-based home/away, fixed
  2026-08-06.** `_pa_round_games()` previously just walked each row's
  ladder climb assuming the tier entrant (row[2]/row[3]/row[4]) was fixed
  and un-conflict-checked, and had the round-(N-1) *survivor* always host
  round N regardless of seed. Both were wrong:
  - **Home/away must be seed-based at every round, not "survivor hosts".**
    An upset winner (the worse seed) does not suddenly host just because
    they won — Draw still seats the lower/better seed, Process the
    higher/worse seed, exactly like round 1. This is the same class of
    bug already found and fixed once for RDS Cup's Round 2
    (winner-rendered-as-home instead of seed-based). Fixed by tracking
    each survivor's *own* seed forward through every round they win
    (never reassigned just because they keep winning) and comparing it
    against the tier entrant's seed at each round.
  - **Conflict resolution (the 5-step order below) now also runs at
    rounds 2-4**, restricted each round to swaps within that round's own
    tier-seed range (65-96 for round 2, 33-64 for round 3, 1-32 for round
    4) — only the tier *entrant* is swappable at these rounds (the
    round-(N-1) survivor is a real, already-played result and never
    changes). `_pa_swap_tier_entrants()` is the shared engine for this,
    parallel to round 1's `_pa_swap_pass()`.
  - **Neither round N's pairing nor its conflict resolution can be
    reconstructed by guessing the opponent from a static, once-computed
    seed mapping** — if round N's own resolution swapped an entrant, a
    naive prediction from an unmutated mapping won't match the game that
    was actually played, and a `_real_bracket_winner()` lookup keyed on
    the wrong two names silently returns "not played yet" even though it
    was. `_pa_ladder_walk()` fixes this by walking rounds 1→N **one round
    at a time**, resolving each round's conflicts and using that round's
    own just-computed (correct) pairing to look up its real winner before
    moving to the next round — never guessing.
  - **Settling either bracket's round N+1 pairing now waits for BOTH
    Draw and Process to finish round N first**, not just the bracket
    being asked about (`_pa_bracket_round_complete()`, checked for both
    brackets before generating anything past round 1) — per explicit
    instruction. This also happens to be structurally required anyway:
    the cross-bracket duplicate check (step 2) needs the other bracket's
    same-round pairing to exist before it can run.
  - **Rounds 5-8 (the champions bracket) DO get conflict resolution as of
    2026-09-12** — `_pa_champions_walk` / `_pa_swap_champion_opponents`.
    They were deferred for a long time on the grounds that a swap there
    "would mean reassigning which two already-decided champions face each
    other (a re-seeding operation, not a seed-holder swap) — a genuinely
    different, untested mechanism with no real data to validate against
    yet (round 4 hasn't been played)." Round 4 has since been played in
    both brackets, so that reason expired; see "The deferral was read as
    the rule" below for how it then caused a real bug.
  - **Verified via a full synthetic walk** (a scratch copy of the
    database, not the real one): round 2 is correctly gated (`None`)
    until both brackets finish round 1, and remains gated symmetrically
    if only one bracket finishes a later round while the other hasn't
    (checked explicitly for round 3 with round 2 lopsided); an engineered
    round-1 upset confirmed the survivor does *not* automatically host
    round 2 (the better seed does, regardless of who won); all 5
    naturally-occurring same-region round-2 collisions (Draw) resolved to
    zero after conflict resolution; a full round 1→7 walk with synthetic
    results produced the exact expected game count every round (32, 32,
    32, 32, 16, 8, 4) with no blocked/failed step; and `_games_for_event`
    (the real matchday-export consumer) was confirmed working end to end
    against the synthetic walk's round 3 data.

**The deferral was read as the rule, corrected 2026-09-12.** The
conflict-resolution rules scope themselves by **round 6** (steps 1 and 3,
same-region/same-division) and by **the mutual semifinal** (step 2, a
Process pairing repeating a Draw pairing). They have never said "rounds
2-4" — that was only ever how far the implementation reached, because
rounds 5+ were deferred.

That distinction got lost. On 2026-09-12, the PA tab's Conflict Resolution
Log emptied itself: `build_pa_cup()` reassigned `swap_log` on every
iteration, so once both brackets finished round 4 and round 5's pairing
became resolvable, round 5's empty log (empty because nothing computed it)
overwrote the real rounds 2-4 history. The first fix introduced a
`PA_CONFLICT_LAST_ROUND = 4` cap — which stopped the symptom by writing the
**gap** down as though it were the rule. It was reverted the same day.

**The actual fix is to resolve rounds 5-8.** Scoping now follows the rules:
`PA_REGION_CHECK_LAST_ROUND = 6` bounds steps 1 and 3, and step 2 is bounded
by `PA_BRACKET_LAST_ROUND` (every bracket round, since the mutual semifinal
is what follows round 8). It was not hypothetical — round 5 was already on
the dashboard carrying three unresolved violations: Nimbasa City vs
Accumula Town (both Vertress) and Lilycove City vs Cherrygrove City (both
division 3) in the Draw, Snowpoint City vs Eterna City (both LilyValley) in
the Process.

`_pa_champions_walk` is the champions-bracket counterpart of
`_pa_ladder_walk` and keeps its central discipline: walk one round at a
time, resolve that round's conflicts, and look up the round's real winner
from the pairing **just resolved** rather than a guessed one. Its swap log
prepends the ladder walk's own rounds 2-4 log, so any single call's log is
self-complete from round 2 — which is what let `build_pa_cup()`'s cap be
deleted outright rather than re-tuned.

The swap mechanism differs from rounds 1-4 and the difference is the point
the old deferral was gesturing at:

- **Rounds 1-4** protect the fresh tier entrant and move the *survivor*
  visiting it. **Rounds 5-8** have no fresh seeds at all, so per "from that
  point forward, the higher seed should always be protected in its
  pathway", the better-seeded side of each game is the anchor and the
  worse-seeded side is exchanged with another game's worse side.
- **Game order is never disturbed**, only which worse-seeded team sits in
  each game — so the bracket's own adjacency into the next round survives a
  swap intact.
- **Two different seeds are in play here and conflating them is easy.**
  Bracket structure, protection and the top/bottom-half test all use each
  row's permanent *identity* seed (1-32, the `33-i` that also sets the
  round-5 pairing), because that is what defines a champion's pathway.
  **Hosting still uses the team's own 1-160 entry seed**, the same as every
  other PA round. This is the one reading chosen rather than given.

**A separate, pre-existing bug fell out of the rewrite**: `_pa_round_games`
returned rounds 5-8 as `(better_seed, worse_seed)` unconditionally, but
`_games_for_event` treats that tuple as `(home, away)`. So the Draw seated
the wrong team in 6 of 16 round-5 games and the Process in **12 of 16** —
the Process's rule is that the *worse* seed hosts, and it was getting the
opposite in every game the seeds didn't happen to order correctly. It never
showed on the dashboard because `pa_cup_round_preview()` re-derives
orientation from seeds itself; it would have shown the moment week 16's
matchday was exported. Rounds 1-4 were always correct (`final_pairs`
applies `home_is_lower_seed`).

Verified: round 5 goes from 3 violations to 0 with 3 swaps, 32 distinct
teams per bracket, 0 cross-bracket repeats and 0 hosting-rule violations; a
synthetic walk through rounds 5-8 holds every invariant (16/8/4/2 games per
bracket, no swap crossing the half boundary, rounds 7-8 logging no
region/division entries since those checks stop after round 6, and a round-6
same-division pairing correctly left standing and logged under rule 5 rather
than forced); regenerating moves only `PA_ROUND_PAIRINGS` (round 5 alone,
rounds 2-4 byte-identical), `PA_SWAP_LOG` and `TEAMS_EXPORT_TSV`; Playwright
reads 42 rows off the rendered log — 10/14/7/8/3 across rounds 1-5 — with
zero `pageerror` events.

**Worth keeping in mind generally:** a deferral note and a rule read almost
the same after a few months, especially when the deferral has a good reason
attached. This one even carried its own expiry ("round 4 hasn't been
played") and still got mistaken for the spec. When something looks like a
boundary, check whether the rules name it before encoding it.

**Round-1 pairing bug, found and fixed 2026-08-06.** The formula above
replaces an earlier, wrong version that paired row *i* as `(128+i) vs
(96+i)` for round 1, `64+i` for round 2, and `i` (unmirrored) for round
4 — a naive first-half/second-half split rather than genuine seed-vs-
mirror-seed bracket seeding. It looked plausible (it does produce 32
valid, non-overlapping tier-D pairs) but paired the wrong opponents
entirely — e.g. it paired seed 129 with seed 97 in round 1, when the
correct opponent for seed 97 is seed 160. This was caught only after real
PA Cup Draw Round 1 results were entered against it; those 32 games (round
12) were deleted from the database along with their `team_round_ratings`
and `fatigue_deltas` rows, `results/2026-w6-tue-pa-draw-1.csv` was
removed, and the dashboard was regenerated back to its round-11 state
before the formula fix went in. The corrected version was derived and
verified directly against a user-supplied worked example (`160 vs 97,
winner vs 96, winner vs 33, winner vs 32, winner vs 1` for row 1; `159 vs
98, winner vs 95, winner vs 34, winner vs 31, winner vs 2` for row 2),
including the round-5 continuation, which falls out correctly from the
existing unchanged `k vs 33-k` rule once round 4's identity is fixed to
`33-i`. `pa_cup_ladder_rows()` is the only function that needed to change
— every downstream consumer (`pa_cup_round1_pairs`, `_pa_round_games`,
`resolve_pa_cup_conflicts`) is written generically against `row[0..4]`
and needed no changes. Confirmed all 160 seeds still appear exactly once
across the 32 rows, and Draw/Process home-seed rules still hold.

A **second bug was exposed by the revert**, unrelated to the pairing
formula itself: `regenerate_dashboard.py`'s `build_pa_cup()` always
projected a "next round" preview one past whatever's been played, with no
floor — once round 1's results were deleted, "next round" became round 1
again, which duplicated the tab's own static "Round 1" section under a
hardcoded, wrong "Round 2 (projected)" label (the dashboard JS never
computed that number from the actual preview, it was a literal string).
Fixed by (1) not generating any preview at all until round 1 itself is
actually complete — round 1's own structural pairing is already shown by
the static section, so "previewing" it before it's played is redundant —
and (2) embedding the real target round number as `PA_ROUND_PREVIEW.round`
so the dashboard's label (`` `Round ${PA_ROUND_PREVIEW.round} (projected)` ``)
is correct at every future round, not just round 2.

**Draw seeding**: real, from the PA Cup tab (`pa_cup_seeds.json`).
**Process seeding**: a real, separately-provided seed list
(`pa_process_real_seeds_v2.json`) — not derived from Draw. Two rounds of
data-entry corrections were needed before it was trusted (team name
mismatches — "Resort Area"/"Battle Zone", "Fight Area"/"Cabo Poco" — and
later a full corrected re-send). Always verify a fresh Process list covers
the same 160 teams as Draw before using it.

**Conflict-resolution order of operations, fixed 2026-08-06 (per explicit
instruction — Draw now gets checked too, not just Process). Originally
built for round 1 only; extended to rounds 2-4 the same day and to rounds
5-8 on 2026-09-12 — this exact 5-step order applies at every round, just
scoped each round to that round's own swappable pool. The parenthesised
reaches below are the rules' own and are the authority on scope
(`PA_REGION_CHECK_LAST_ROUND = 6` for steps 1 and 3; step 2 runs every
bracket round). Per explicit instruction 2026-09-12, "up until round 6"
is read as **inclusive** — rounds 1-6 get those two checks:**
1. (up until round 6) Check the **Draw** for same-region/same-division
   pairings and make appropriate switches.
2. (up until the mutual semifinal) Check the **Process** for any
   pairing that repeats a Draw pairing from any round (including the
   current one) — checked against Draw's now-final round-1 pairings from
   step 1 — and make appropriate switches.
3. (up until round 6) Check the **Process** for same-region/same-division
   pairings and make appropriate switches.
4. Confirm both brackets satisfy all three rules in their final state —
   catches any interaction between steps 1–3 (e.g. step 3 accidentally
   reintroducing a duplicate step 2 already fixed).
5. If no valid swap satisfies a rule, the **original pairing stands**,
   even if it breaks that rule — this always wins, and nothing is ever
   left artificially "fixed" by force.

**Top-half/bottom-half must never cross** during any swap, in either
bracket — with N teams remaining, a team can only be swapped with another
team in the same half (e.g. with 16 remaining, seed #8 can never end up
facing #7 or better; only #9–16 are valid swap targets).

Implemented in `resolve_pa_cup_conflicts()`, built on a shared
`_pa_swap_opponents()` engine (one violation-predicate pass over one
bracket) reused for all three steps and for every round (1-4 alike).
Previously Draw was never modified at all — only Process got conflict
resolution, on the theory that Draw's seeding was "real, authoritative
data" that shouldn't be touched. That was wrong: found when a same-region
Draw round-1 pairing (Lacunosa Town vs Driftveil City, both Vertress)
surfaced after the round-1 pairing formula fix above. Cross-checking
confirmed it wasn't a coincidence — the *old* (also-wrong) pairing formula
produced zero same-region Draw collisions across all 32 rows, the
corrected formula produced three, meaning Draw's seed-to-team assignment
was never actually conflict-free by construction; it just happened not to
collide under the old, incorrect pairing. Tested against a fully
duplicate Process list (worst case) before trusting it on real data, and
reverified end-to-end after this fix: 0 unresolved region/division
violations in either bracket, 0 repeat matchups between final Draw and
final Process, all 160 seeds still map to exactly one team in each
bracket, and every swap made respects the top/bottom-half boundary.

**A swap reassigns who a row is PAIRED against, never who holds a seed —
fixed 2026-08-06, same day.** The first version of this mutated
`seed_to_team` directly: "swap seed #157 and #158" meant the two seeds'
*occupants* traded places (whichever team was at 157 moved to 158 and
vice versa). Per explicit instruction, this is wrong for two reasons: (1)
a team's seed is supposed to be permanent, real data (`pa_cup_seeds.json`
et al. assign each team a seed once, for the whole tournament — that's
the whole reason Draw's seeding was ever treated as untouchable in the
first place, before the bug above), and (2) it silently reassigns a
team's *future bracket path* — if Lacunosa Town (seed 157, row 4) got
moved to seed 158 by a swap, they'd inherit row 3's tier entrants
(94/35/30) going forward instead of row 4's own (93/36/29), which the
row's own seed (157) is permanently supposed to determine, regardless of
who they end up playing in any given round. The fix: swap the *opponent
assignment* between the two rows instead — "row 4 (seed 157, permanently
Lacunosa) now plays seed 99 instead of seed 100; row 3 (seed 158,
permanently whoever) now plays seed 100 instead of seed 99" — so
`seed_to_team` is 100% immutable everywhere, for every round, and a
team's own future path is always tied to their own permanent seed. Which
two teams actually face each other can still shift due to a swap — that
tournament-structure churn is the explicitly intended effect, not
something to avoid. `pa_cup_round1_pairs()` now takes an explicit
`opponent_of: {row_idx: seed}` mapping (defaulting to each row's own
formula-derived opponent, mutable only via conflict resolution) instead
of reading `row[1]` directly; the same pattern extends to rounds 2-4's
tier entrants via `_pa_default_opponents(round_slot)`. Reverified:
`draw_round1[0]` still reads `160 vs 97`, the swap log now names the two
*opponent* seeds exchanged instead of the row's own permanent seeds,
`draw_seed_to_team[157]`/`[158]` are confirmed unchanged from the raw
JSON after resolution, and a full synthetic round 1→2 walk confirmed a
team who wins round 1 via a swapped-in opponent still climbs through
their *own* row's round-2 tier-C entrant, not the row they were paired
against.

**Swap search direction was backwards, fixed 2026-08-06, same day.** The
first version of the opponent-swap fix above searched worse (numerically
higher) candidate seeds before better ones, which produced `Swapped seed
#100 ↔ #101` for row 4's conflict — pairing row 4 (seed 157) with row 5's
opponent instead of row 3's, even though row 3 (seed 158) is the
immediately-adjacent, next-worse-seeded row and was available. The bug:
opponent seed and anchor seed move in *opposite* directions as row index
increases (anchor `161-i` decreases, round-1 opponent `96+i` increases),
so searching opponent seeds ascending-first actually reaches toward
*better*-anchored neighboring rows first, backwards from "try the next-
worst row first." Per explicit instruction, corrected `_pa_swap_opponents`
to search the next-*better* (numerically lower) opponent seed first, then
the next after that, and so on, only falling back to worse (higher) seeds
once every better option in the same half is exhausted. Row 4's conflict
now resolves to `Swapped seed #100 ↔ #99` — `157 vs 99`, `158 vs 100` —
exactly the original worked example. Reverified end-to-end after the
fix: 0 unresolved region/division violations in either bracket, 0 repeat
matchups between final Draw and Process, all 160 seeds still map to
exactly one team in each bracket, every swap still respects the
top/bottom-half boundary, and a full synthetic round 1-7 walk (including
an engineered round-1 upset) still produces the exact expected game count
every round with correct seed-based hosting throughout.

**Wrong side was being swapped, fixed 2026-08-06, same day (again).**
Both versions above still swapped the WRONG side. Round 1's two seeds per
row are `row[0]` (the worse seed, 129-160) and `row[1]` (the better seed,
97-128) — every previous version treated `row[0]` as the row's permanent,
future-owning anchor and swapped `row[1]` (the opponent) between rows.
Per explicit instruction, this is backwards: **the better seed (row[1])
must be the one that "stays in order" — its own future path (round 2-4
tier entrants) is permanently tied to its own row, unaffected by any
swap. It's the worse seed (row[0]) that moves between rows when a
conflict needs resolving** — for row 4's conflict, that means swapping
seed #157 with seed #158 directly (row 4's worse seed for row 3's), not
swapping their opponents (#100/#99). The result happens to produce the
same final PAIRING either way (157 vs 99, 158 vs 100) — but the two
models disagree on what happens to a worse seed's own future path if it
wins: under the wrong (row[1]-swapped) model, if Pewter City (seed 99)
had been swapped into a different row and won, they'd have inherited
that row's tier entrants instead of their own. Under the corrected
model, the *worse* seed inherits whichever row it currently occupies if
it wins (not protected — "until they become lower seeds in future
matches," per explicit instruction), while the *better* seed's own path
never changes regardless of who it's made to play. Fixed by swapping
`pa_cup_round1_pairs()`'s anchor from `row[0]` to `row[1]`,
`_pa_default_opponents(0)` (not `1`) as round 1's swappable default, and
`resolve_pa_cup_conflicts()`'s `draw_rows`/`process_rows` anchor lookup
from `row[0]` to `row[1]` — with the search-direction fix from the
previous entry reverted back to worse-first-then-better, since the
swappable pool is now the *worse* half (129-160) and "next-lowest
seeded" (next worse) means ascending toward higher numbers within it.
Verified: the swap log now reads `Swapped seed #157 ↔ #158` directly
(matching the exact original instruction), Lacunosa Town (157) and
Driftveil City (100) are both still permanently at their own seeds, and
a synthetic test confirmed a swapped-in worse seed (e.g. seed 158) that
wins round 1 correctly advances via *its current row's* own tier-C
entrant (row 4's, since that's where it was placed) rather than the row
it was swapped out of — while the anchor side never needs this check,
since its own row identity is never reassigned in the first place.
Reverified end-to-end after this fix too: 0 unresolved violations, 0
duplicate matchups, all 160 seeds still map to exactly one team per
bracket, boundary respected, and a full synthetic round 1-7 walk still
produces the correct game count every round.

**Entrant-protection generalized to rounds 2-4, fixed 2026-08-06, same
day (again).** The "open question" above was resolved by explicit
instruction: "In R1, 97-128 are protected. In R2, 65-96 are protected. In
R3, 33-64 are protected. In R4, 1-32 are protected. From that point
forward, the higher seed should always be protected in its pathway." So
each round's own **tier entrant** (the fresh seed entering that round —
65-96 for round 2, 33-64 for round 3, 1-32 for round 4) is now the
permanent anchor, mirroring round 1's row[1]: it never leaves its own
row's displayed game, and a swap only ever reassigns which OTHER row's
historical **survivor** is currently sent to visit and play it
(`_pa_swap_survivors`, replacing the old `_pa_swap_opponents`-based
tier-entrant swapping, which had this backwards — it treated the
survivor as fixed and the entrant as the swappable side).

This is deliberately asymmetric with round 1, not just a copy-paste: round
1's two seeds (row[0], row[1]) are both fresh, never-yet-played seeds, so
either one can in principle be the swappable side. Rounds 2-4 are
different — the "other" side isn't a fresh seed, it's a **survivor**, a
real, already-decided result from a prior round. Letting the *entrant*
be the swappable side (mirroring round 1's original, wrong model) opens a
structural trap that round 1 doesn't have: if the entrant could leave its
own row's game, a row could end up with **zero winners** — its own
survivor loses to a visiting entrant, while its own (now-relocated)
entrant separately loses a different game elsewhere — stranding that row
with nobody to carry its future into the next round. Keeping the entrant
permanently anchored to its own row's game avoids this by construction:
every row always produces exactly one winner each round, no exceptions.
Confirmed via a synthetic round-2 scenario (forced same-region conflict,
entrant vs its assigned visitor): the visiting survivor, once swapped in
to face a different row's entrant and winning, correctly inherits *that
row's* own round-3 tier-B entrant going forward — not the row it
originated from.

**Separate, previously-undocumented bug found and fixed the same day:
round 4 → round 5 handoff used entrants, not winners.** While wiring up
the rounds 2-4 rewrite, `_pa_round_games()`'s rounds 5-7 branch was found
calling `_pa_ladder_walk(conn, 4, ...)` and treating its returned
survivors as "round 4's winners" — but `_pa_ladder_walk(conn, 4, ...)`
actually returns who's alive **entering** round 4 (i.e. about to play
round 4), not who won it. This is a real semantic mismatch that
game-count-only tests never caught, since it doesn't change *how many*
teams reach round 5, only *which* teams. Fixed by adding a
`resolve_final_round=True` parameter to `_pa_ladder_walk`: when set, it
additionally advances `draw_survivors`/`process_survivors` through
`up_to_round`'s own real results (requiring `up_to_round` itself, not
just `up_to_round - 1`, to be complete), returning who's alive heading
into `up_to_round + 1`. `_pa_round_games()`'s rounds 5-7 branch now calls
`_pa_ladder_walk(conn, 4, team_region, team_division,
resolve_final_round=True)` instead. Verified synthetically: all 32
round-4 survivors obtained this way actually appear in round 5's field,
32/32.

## World Championship qualification (Qualification tab)

Added 2026-09-10. A 48-team field, built by `world_championship_field()`
and rendered by the dashboard's **Qualification** tab. The engine decides
the field and the JS only draws it, so the two can't disagree about who
is in.

**Bids, awarded strictly in this order** (the order is the rule -- earlier
categories win a contested team, and everything downstream reacts):
1. Divisions 1-6: **5 / 4 / 3 / 3 / 2 / 1** = 18.
2. PA Cup semifinalists: 4 (max).
3. RDS Cup finalists: **exactly 2 per cup**, 6 across Ribbon/Dream/Star.
4. Regional Tournaments: 3 bids each from the top 3 regions by allocation
   ranking, 2 each from the next 4, 1 each from the last 3 = 20.
5. Highest OVR remaining, as needed.

**No team is invited twice.** A bid whose team is already in falls to its
own category's replacement chain: PA -> best-seeded losing quarterfinalist;
RDS -> best-seeded losing semifinalist **of that same cup** (a Ribbon bid
never falls to a Star team); RT -> that region's seed order
(#1 seed, other finalist, best-seeded losing semifinalist, other losing
semifinalist), which under a chalk projection is simply seeds 1-4. A bid
whose chain is exhausted -- or that a short category never produced --
passes to the OVR pool. **The field therefore always closes at exactly
48**, and `passed_to_at_large` records every bid that fell through, naming
the team it would have gone to and where that team is already in, so a
redundant bid is visible rather than silently absorbed.

**"Max" applies to PA only -- getting that wrong was a real bug, corrected
2026-09-10 on being asked directly whether each RDS cup provides 2.** Draw
and Process are two parallel brackets over the *same* teams, so one team
can be alive in both. The first draft projected each bracket independently
and double-counted (`pa_semifinalists` read `[Canalave, Canalave, Nimbasa,
Nimbasa]`); deduplicating was right, but it was then applied to **both**
cups on the assumption that a double qualifier shrinks the category. That
holds for PA and **not** for RDS:

- **PA: 4 semifinalists, max.** Each bracket's round 8 sends 2 into the
  mutual semifinal, and a team coming through both sides occupies one slot
  instead of two, so the semifinal field can genuinely be 3 or 2 distinct
  teams. PA currently projects only **2**. (`pa_losing_qf`, the
  replacement pool, still means what it always did: the round-8 entrants —
  each bracket's own quarterfinal field — who didn't get through, so the
  quarterfinal-dropping change above left this computation untouched.)
- **RDS: exactly 2 per cup, always.** A final has two participants by
  definition. A team topping both of its cup's brackets does *not* halve
  that -- per the mutual-stage rules above, the double qualifier byes
  straight to the final while the other two play the lone semifinal, so
  the second finalist still exists. Projecting "top 1 alive per bracket,
  deduplicated" gave each cup a single finalist and quietly sent 3 bids
  to at-large that were never the OVR pool's to take. Corrected to rank
  the **mutual-stage field** -- `dedupe(draw[:2] + process[:2])` -- and
  take its top two; the remainder are that cup's losing semifinalists.
  Note the field size follows the same rules: 3 distinct teams (one bye +
  one semifinal) yields exactly **1** losing semifinalist, 4 distinct
  yields 2. Both are correct, not a shortfall.

**Everything on this tab is a projection**, per explicit instruction
("project from current standings, but make note of who is projected"),
and each bid carries a `basis` saying which kind:
- *Current League standings* -- division bids. Real played games, but the
  season is only part-way through its 15 league rounds (9 as of round 31).
- *Projected from teams still alive* -- PA/RDS. Nothing past PA round 3 /
  RDS round 4 has been played and neither mutual stage is a playable event
  (both raise `NotImplementedError`), so advancement is projected by
  current OVR rank among each bracket's survivors. `_cup_survivors()`
  defines "alive" as *has not lost in this cup+bracket*, which
  deliberately counts teams awaiting a first game -- byes, and PA's tier
  seeds that enter at rounds 2/3/4 -- as alive. Anything stricter would
  wrongly eliminate the entire top tier mid-ladder.
- *Projected on seed (chalk)* -- Regional Tournaments. Zero RT games have
  been played. Under chalk (better seed always advances) the lane
  structure puts seeds 1/2/3 in the top three, which is also exactly the
  replacement chain the rules describe.

**The RDS pool was briefly shared across all three cups and that was
wrong** -- resolved 2026-09-10 by explicit clarification. Each cup awards
its own 2 bids and replaces from its own losing semifinalists only. The
one reading still chosen rather than given: "highest-seeded" everywhere
means the **best (lowest-numbered)** seed, matching this file's usage
everywhere else. For PA, where a team holds a different seed in each
bracket, its *better* seed is the one used.

### Allocation ranking (which regions get 3, 2, or 1 RT bids)

`region_allocation_ranking()`. Per region:
`(S9/2 + S8/3 + S7/6) * 100`, rounded to 2dp. The weights sum to 1, so the
blend stays on the same scale as a single season's strength multiplier and
x100 lands near 100.

S9 is the region's **live** strength multiplier from
`compute_strength_breakdown()`. S8 and S7 come from
`REGION_PRIOR_STRENGTH`, supplied already z-scored within each season
(each column is exactly mean 0, pstdev 1 -- verified). Those are **not**
on the same scale as a stored strength multiplier, so
`_normalized_prior_strength()` pushes them through the *same* transform
`_strength_formula_breakdown()` applies to its own weighted sums --
`1.0 + STRENGTH_TANH_STRETCH * tanh(x / (pstdev * STRENGTH_TANH_K_MULTIPLIER))`
-- which is what puts all three seasons on one 1.0-centered scale before
blending. Since each prior column has pstdev exactly 1, k works out to
exactly 2 for both.

**This ranking is derived, not static** -- the S9 term moves with every
result, so the tier boundaries move too -- and they demonstrably do. Over
rounds 28-31 alone, Kalosite and Lanakila swapped 5th/6th (both 2-bid, so
no bid changed hands), and the 3-bid/2-bid line between Terastal and
Silver went from 0.71 apart (108.49 / 107.78 at round 27) to 2.96 apart
(111.97 / 109.01 at round 31). Never treat a printed allocation table as
settled; regenerate it.

## Regional Tournament (postseason)

One per region (10 total), 16 teams seeded by final **Regional Standings**
(`regional_standings_seeds()` — reuses the exact same group-based tiebreak
algorithm as the dashboard's Standings tab, ported carefully from the JS:
complete round-robin or a sweep within a tied group uses head-to-head,
otherwise falls straight through to TB score. A naive pairwise-H2H
shortcut was tried first and gave a real wrong answer — caught by
cross-checking against the already-validated LilyValley standings, where
Eterna/Mossui/Celestic never played each other and should resolve by DSCR).

**Structure**: 4 independent "lanes" (`REGIONAL_TOURNAMENT_LANES`), lane =
`[MD1 away-seed, MD1 home-seed, MD2/3 entrant, MD4/5 entrant]`:
```
16 9 8 1
15 10 7 2
14 11 6 3
13 12 5 4
```
- MD1: single game, better (lower-numbered) seed hosts.
- MD2/3 and MD4/5: two-legged ties. Leg 1 hosted by the **worse**-seeded of
  the two teams (using each team's own original seed), leg 2 by the
  **better**-seeded. Same rule confirmed to also govern MD6/7 (semifinal)
  and MD8/9 (final) — not a separate convention.
- Semifinal (MD6/7): lane 4's survivor vs lane 1's, lane 2's vs lane 3's
  (given directly, not inferred).
- Game type is Playoffs (`P`), tagged with `host_region` = the region and
  `cup_round` = the matchday number (1-9) for completion-checking (`_rt_*`
  helper functions look games up by `host_region` + `cup_round`, not
  `cup_name`/`cup_bracket` the way RDS/PA do).
- Calendar: weeks 24-26, `("RT", matchday)` events, confirmed placement.

## Region colors & Region Climate (confirmed against DECKFIELD's real code)

`REGION_COLORS` (bright/dark hex per region) matches DECKFIELD's own
`REGION_COLORS` **exactly** — pulled directly from that project's code, not
picked independently, so the two stay visually consistent. Used for: the
Rankings tab's Reg/Div badge, RL Strength's Regional Strength labels,
Standings/Regional Playoffs box headers, and winner-name highlighting in
Schedule/RDS/PA Cup tabs (each winner colored by *their own* region,
resolved via a name→region lookup, not a fixed green).

**`export_region_climate_for_deckfield()`** generates DECKFIELD's actual
"Region Climate" input (confirmed against its own `parseClimatePaste`) —
**not** the same thing as on-screen Weather (DECKFIELD derives
Perfect/Good/Fair/Bad/Terrible from this value via its own thresholds in
`computeWeatherFromClimate`). Formula: `sqrt(average of three uniform(0,1)
draws)`, fresh per region, regenerate every matchday. The dashboard's
Next Matchday tab has a client-side "Reroll" button using the identical
formula, so a fresh roll doesn't require regenerating the whole dashboard.

**Region naming mismatch, fixed**: this database's internal region field
is `"LilyValley"` (no space), but DECKFIELD's `TEAM_REGION.home =
homeTeam['Region']` is taken **verbatim** from the roster with zero
transformation, and `REGION_COLORS['Lily Valley']` (with the space) is
what it actually looks up. `region_display_name()` / `REGION_DISPLAY_NAME`
converts before any export leaves this engine — every other region name is
already identical either way, so this is the *only* region needing the
conversion, but it's easy to reintroduce if a new export is added without
routing through `region_display_name()`.

## DECKFIELD export formats (verified against its real parser code, not guessed)

All three exports below were checked directly against DECKFIELD's actual
`parseRosterPaste` / `parseSchedulePaste` / `parseClimatePaste` /
`ROSTER_COLUMNS` / `SCHEDULE_COLUMNS` — this caught real bugs that guessing
from a conversation summary alone had missed:

1. **Delimiter is tab (`\t`) for all three**, not comma. The Schedule
   export used commas until this was checked directly against
   `parseSchedulePaste`, which splits on `'\t'` — every matchday export
   before this fix would have silently failed to parse in DECKFIELD.
2. **All three importers default to expecting a header row**
   (`rosterHasHeader`, `schedHasHeader`, `climateHasHeader` all default
   `checked`). None of the exports included one before this was checked —
   the first real data row would have been silently discarded as a header
   every time. All three now include the exact header DECKFIELD expects
   (`ROSTER_COLUMNS`, `SCHEDULE_COLUMNS` = `['Away Team Rank', 'Home Team
   Rank', 'Adv']`, `['Region', 'Region Climate']`).
3. **Region naming** — see above.

`export_teams_for_deckfield()`: PF/PA/Skill Rating use the normalized
0–100 OVR components (same numbers the dashboard's Rankings tab shows), not
raw season totals — confirmed intentional. **DSCR is the exception, fixed
2026-08-05**: it was also exported as the normalized 0–100 `dscr_comp`
component, but `deckfield.html`'s own Eye Test Bonus formula expects a raw
0–10 D-Sqrt value (`TEAM_DSCR`'s placeholder is `7`, not `70`, and it's
read straight off the pasted roster with no scaling) — a 10x-inflated
DSCR term would have thrown off Eye Test Bonus, and therefore Spread, on
every real match. Now uses `d_sqrt_raw` instead. Secondary Type is a fresh
random 1-18 generated by **this** export (`random.randint(1, 18)`), not
read from anywhere — the dashboard owns it, regenerated new every export
(i.e. new each matchday), not DECKFIELD itself. Accolades needed sanitizing:
the source data has embedded double-newlines separating distinct accolades
and single-newlines as pure line-wrapping within one — collapsed to
`"; "`-separated single-line text so one team never spans multiple TSV
rows.

## Dashboard

Currently a **static HTML snapshot** — one big file with data embedded as
inline `const X = {...}` JSON blocks. Tabs: Rankings, Standings, RL
Strength, Schedule, RDS Cup, PA Cup, Next Matchday (matchup table +
copy-paste boxes for DECKFIELD's Schedule/Teams/Region Climate inputs),
Calendar (full 26-week schedule, played/next/pending status), Regional
Playoffs (all 10 regions shown at once in a grid, no dropdown), Rank/Elo
History (below).

**Per explicit instruction, the dashboard must always be regenerated
whenever new results are added.** `regenerate_dashboard.py` (added
2026-08-05) does this: `python3 regenerate_dashboard.py` after any
`deckfield add-results`, rewrites `DATA` (Rankings tab; Standings derives
from it client-side), `TEAMS_EXPORT_TSV`, `NEXT_MATCHDAY_DATA`,
`CALENDAR_DATA`, `RANK_ELO_HISTORY`, `PA_REAL_RESULTS`/
`PA_ROUND_PREVIEW` (PA Cup tab), and `STRENGTH_DATA` (RL Strength tab) in
place. Each reconstruction was
validated by recomputing at round 11 (the last state before this script
existed) and confirming a **byte-identical** match against the
already-published `DATA`/`CALENDAR_DATA` at that round — including the
`dscr_regional_avg`/`dscr_league_avg` fields, confirmed to be the average
of `games.dscr_a`/`dscr_b` (the raw per-game DMAX score, not `d_sqrt_raw`)
across a team's Regional-only/League-only games respectively, and the
`log` field, confirmed to be each team's `[opponent_dex, game_type,
result_a]` history in round order.

**PA Cup real results, added 2026-08-05**: `PA_CUP_DATA` only ever held
structural seeding (`draw_round1`/`process_round1`/`swap_log`) — there was
no `PA_REAL_RESULTS` counterpart to RDS Cup's `CUP_REAL_RESULTS`, so the
PA Cup tab never showed real results at all, round 1 included. Fixed the
same way RDS Cup's Round 2 bug was fixed earlier: added
`pa_cup_real_results()` (winner/loser/scores per bracket+round, straight
from `games` where `cup_name='PA'`) and `pa_cup_round_preview()` (a
"what's next" projection one round past whatever's been played, resolved
through real winners via the existing `_pa_round_games()` — same idea as
RDS Cup's `RDS_ROUND3`), and reused the `rdsRound1RowHtml`-style seed-aware
win/loss rendering (`pacupRowHtml`) instead of the old placeholder that
never showed a score. Verified against real data: Alfornada (seed 127,
home) beat Boyleland (seed 159, away) 39–18 in Draw round 1, and the tab
now shows that score with Alfornada's name colored as the winner, plus a
projected Round 2 for Draw (Process round 1 hasn't been played yet, so its
tab correctly still shows only the unplayed structural Round 1, no
scores/highlighting — a live per-bracket regression check, not a
special case).

**`STRENGTH_DATA` (RL Strength tab), fixed 2026-08-05.** Originally
approached as a from-scratch reverse-engineering problem against the
*old workbook's* published region-aggregate numbers — z-scoring each
region's/division's own **average** of a stat (rank/OVR/wins/etc.)
against the population of 10 region (or division) averages. That
approach exactly reproduced `z_dscr`/`z_elo`/`z_tot` and the overall
weighted-sum + tanh combination formula, but never landed on `z_rank`,
`z_ovr`, or `z_wins` under any population/stdev variant tried — because
the old workbook's Rank/OVR/Wins values are frozen at a stale formula
version (documented elsewhere in this file: the workbook's own current
Rank/OVR columns only agree with this engine's live values on 10/160
teams), so no z-score methodology built on *this engine's* numbers was
ever going to reproduce numbers computed from *different underlying
values*. Chasing the published numbers was the wrong goal.

The actual fix: this exact 7-component z-scored formula (Rank ×2 flipped,
OVR ×2, Wins ×1, DSCR ×2, Elo ×1, TOT ×1, GI ×1 → weighted sum → `1.0 +
0.75*tanh(weighted / (2*population_stdev))`) was **already implemented**
in the engine, computed at the region/division level, as an internal step
of `compute_strength_scores()` / `_strength_formula()` — the function that
produces each team's stored `rlstr_own` (`rlstr = (regional_strength[region]
+ league_strength[division]) / 2`, confirmed correct and already in
production). It just discarded the per-component z-scores and returned
only the final blended multiplier. The dashboard's RL Strength tab wants
exactly those discarded intermediates, not a separate reconstruction.
`_strength_formula` was split into `_strength_formula_breakdown()`
(returns the full per-group dict: `z_rank`, `z_ovr`, `z_wins`, `z_dscr`,
`z_elo`, `z_tot`, `z_gi`, `weighted`, `final`), with `compute_strength_scores()`
now just extracting `"final"` from it for backward compatibility. New
public `compute_strength_breakdown(season, round_num)` exposes the full
breakdown for both regional and league grouping. Two details worth
remembering: `"wins"` is deliberately *not* same-mode win count (regional
wins average to a fixed 2.5/region under round-robin, which is why that
path never worked) — Regional Strength uses `intl_w` (league + cup +
playoff-finals wins) and League Strength uses `dom_w` (regional +
playoff-finals wins), each varying enough across a group to be meaningful.
And GI is the plain average of `games.interest_score` per team, matching
what `_strength_raw_inputs()` already used for the per-team formula.
Wired into `regenerate_dashboard.py` as `build_strength_data()`.
**Validated exactly**: for every team at the latest round, `(regional_bd[team.region]["final"]
+ league_bd[team.division]["final"]) / 2` reproduces `team_round_ratings.rlstr_own`
byte-for-byte, 160/160 — since it's the same computation, not a parallel
reconstruction that happens to agree.

Everything else (`SCHEDULE_DATA`, `CUP_BRACKET_DATA`/`CUP_REAL_RESULTS`/
`RDS_ROUND2`/`RDS_ROUND3`, `RT_DATA`) is untouched by
`regenerate_dashboard.py` because it's genuinely unaffected by
Regional/League/PA results specifically — not a gap, just out of that
script's scope until something that actually changes those (an RDS round,
a Regional Tournament matchday) comes in.

**`PA_CUP_DATA` was a silent exception to that, found 2026-08-06.** It
looked like the same kind of "out of scope" static data as
`CUP_BRACKET_DATA` etc., but it isn't — it's the *output* of
`pa_cup_ladder_rows()` + `resolve_pa_cup_conflicts()`, baked into the
dashboard once by an early one-time script and never touched again. When
the round-1 pairing formula bug was fixed (see PA Cup section above),
`regenerate_dashboard.py` updated `PA_ROUND_PREVIEW` correctly (it's
genuinely derived fresh from the engine every run) but never touched
`PA_CUP_DATA` — so the tab's own primary "Round 1" section, and its
conflict-resolution log, kept showing the pre-fix pairings even after the
formula was corrected. Caught when asked directly whether conflict
resolution had actually run against the corrected data — it had run, just
against the wrong input, months earlier. Fixed two ways: (1)
`_pa_round1_games()` now returns the swap log it already computed instead
of discarding it, and (2) new `pa_cup_round1_seeding()` wraps it into
`PA_CUP_DATA`'s exact shape (with `home_dex`/`away_dex` added), wired into
`regenerate_dashboard.py` as a genuine per-run rebuild, not a static
constant. Verified: `draw_round1[0]` now reads `160 vs 97` (matching the
corrected formula) and the swap log is a different, freshly-computed list
(rows 9/10/22) from the stale one (rows 12/16) it replaced. The lesson:
"looks like static seed data" and "is actually static" are not the same
thing — worth checking what a dashboard constant is *derived from* before
assuming a formula fix upstream already reached it.

**Conflict Resolution Log only ever showed round 1, found and fixed
2026-08-07.** Same underlying class of bug as the `PA_CUP_DATA` finding
above, at a different layer: `_pa_round_games()`'s rounds 2-4 branch calls
`_pa_ladder_walk()`, which genuinely computes and returns a swap log for
that round (rounds 2-4 do get real conflict resolution, per the
entrant-protection model above) — but `pa_cup_round_preview()` (the
"what's coming up next" preview powering `PA_ROUND_PREVIEW`) only ever
unpacked the *pairings* out of that call and silently dropped the swap
log, so the dashboard's Conflict Resolution Log table only ever rendered
`PA_CUP_DATA.swap_log` (round 1's). Once round 1 finished for both
brackets and a round 2 preview started rendering, this made the log look
like round 2 had zero conflicts, when in reality **14 swaps** were needed
and successfully resolved (7 Draw, 7 Process) — caught only by directly
querying `_pa_ladder_walk(conn, 2, ...)`'s own returned swap log and
finding it non-empty, contradicting what the rendered report showed.
Fixed by having `pa_cup_round_preview()` re-run `_pa_ladder_walk()` for
`target_round` (rounds 2-4 only; rounds 5-7 have no conflict resolution,
so their swap_log is always `[]` -- see the 2026-09-12 entry below for the
bug that empty log later caused) and attach the result as
`preview["swap_log"]`, which `build_pa_cup()` already passes straight
through into `PA_ROUND_PREVIEW`. The dashboard's log table now merges
`PA_CUP_DATA.swap_log` (tagged round 1) with `PA_ROUND_PREVIEW.swap_log`
(already tagged with its own round per entry) and gained a **Round**
column so entries from different rounds don't get confused once round 3+
previews start appearing here too. Also dropped the round-2+ preview
section's `(projected)` suffix in its label — round 2's pairing isn't
speculative once both brackets have finished round 1 (the "wait for both
brackets" gate above already guarantees this), it's the same kind of
fully-resolved, real-winners-pending-play pairing RDS Cup's round 3+ has
always shown without a "projected" qualifier; the label was left over
from language written when only one bracket's round 1 was done and round
2 genuinely could still shift.

**The Conflict Resolution Log emptied itself the moment PA round 5 became
resolvable, found 2026-09-12.** `build_pa_cup()` reassigned `swap_log` on
every iteration of its round walk, so once round 5 became resolvable its
empty log wiped the rounds 2-4 history and the tab fell back to round 1's 10
entries alone. The first fix capped the accumulation at round 4; that was
wrong in premise and was reverted the same day — the log was empty because
rounds 5+ had no conflict resolution implemented, not because the rules stop
at 4. See "The deferral was read as the rule" in the PA Cup section for the
real fix. The guard is gone: every round 2-8 now returns a log that
self-accumulates from round 2.

**`RT_DATA` was the sixth instance of the same silent-staleness bug, found
and fixed 2026-09-03 — and this time the class of bug got a guard.**
`RT_DATA` (Regional Playoffs tab) had been baked in once by an early
one-time script and was never wired into `regenerate_dashboard.py`, exactly
like `PA_CUP_DATA`, `SCHEDULE_DATA`, `CUP_REAL_RESULTS`/`RDS_ROUND2`/
`RDS_ROUND3` and `PA_ROUND_PREVIEW` before it. It is emphatically not
static: every matchup in it comes from `regional_standings_seeds()`, which
re-sorts on the *current* standings, so it drifts further out of date with
every result added. Confirmed stale rather than assumed — regenerating it
moved **27 of the 40** MD1 slots (e.g. Indigo's seed-16 visitor to Pewter
City read Cycling Road when the live standings said Cerulean City).

Fixed with `build_rt_data()` in `regenerate_dashboard.py`, which
accumulates matchday 1 upward and stops at the first one
`regional_tournament_games()` can't resolve yet — the same self-limiting
pattern `rds_cup_round_pairings()` and `build_pa_cup()` already use, so it
covers every matchday played plus exactly one ahead and never guesses past
that. Region keys stay the database's internal names (`LilyValley`, no
space) to match the dashboard's own `REGION_ORDER`; `region_display_name()`
is for DECKFIELD exports, not these keys.

**The real fix is the guard, not the sixth patch.** `DERIVED_CONSTS` /
`STATIC_CONSTS` in `regenerate_dashboard.py` now enumerate every top-level
`const X = ...` block in the dashboard, and `_check_const_manifest()` runs
before anything is rewritten, raising on (a) any constant in neither list
and (b) any constant this script claims to regenerate that has gone missing
from the file. Nothing about a constant's *appearance* distinguishes
"static" from "derived and forgotten" — that's precisely why this bug
recurred six times — so the distinction is written down, with a reason
recorded next to each static entry, and a new dashboard constant can't be
added without someone deciding once which kind it is. Both failure modes
were tested by deliberately introducing them (an unaccounted new constant,
and a renamed derived one) and confirming the run fails.

**A leg-numbering bug in the schedule helpers, found and fixed
2026-09-11 while wiring the RDS mutual stage.** `_schedule_event_key()`
has always given each leg of a two-legged tie its own absolute round (RDS
SF = 39/40, Final = 48/49, PA QF = 57/58, SF = 60/61 -- verified), but
`abs_round_for_event(event)` took only the slot tuple and resolved it by
scanning `WEEKLY_SCHEDULE` for the **first** match, so it could only ever
return leg 1's round. Leg 2's was unreachable through the public helper,
which would have filed leg 2's results under leg 1's round. Fixed by
adding optional `week`/`day` parameters (falling back to the old
first-occurrence behaviour when omitted, which is what every pre-existing
caller wanted) and threading `info["week"]`/`info["day"]` through
`_games_for_event`, `_event_is_played` and both export entry points.

`_event_is_played()` was also returning `False` unconditionally for RDS
SF/Final ("mutual stage -- not modeled/played yet"), which was correct
while nothing could be played but would have stalled `next_matchday()` on
week 15 forever. It now counts real games at that leg.

**The header subtitle was a seventh instance of the same staleness bug —
one layer below the guard, found and fixed 2026-09-08.** `<div
class="subtitle">Season 9 &mdash; through round 11</div>` was plain HTML
text, hand-written once and never wired into `regenerate_dashboard.py`, so
it still read "round 11" while the database had run on to round 27 — the
single most prominent number on the page, wrong by 16 rounds. The
`DERIVED_CONSTS`/`STATIC_CONSTS` manifest could not have caught it:
`_check_const_manifest()` only enumerates `const X = ...` blocks, and this
is markup, not a constant. Fixed with `_replace_subtitle()`, which reads
`MAX(round)` from `games` and rewrites the line every run (idempotent —
re-running leaves it unchanged). The lesson extends the earlier one: the
guard covers *constants*, so anything derived that lives outside a `const`
— page text, a title, a hardcoded count — is still on its own.

**Conflict log named the wrong seeds as swapped, found and fixed
2026-08-07, same day.** Once the round 2-4 conflict log above was
actually visible, real round-2 entries read e.g. "Swapped seed #96 &harr;
#95" — but 96 and 95 are **entrant** seeds, the permanently protected
side per the entrant-protection model (see above: "the higher [entrant]
seed should always be protected in its pathway," never reassigned to a
different row). Reporting them as swapped directly contradicted that
rule. The actual swap (verified against real row 1/row 2 data: row 1's
entrant is seed 96/Wild Area, row 2's is seed 95/Alfornada, neither ever
moves) exchanges which SURVIVOR visits each entrant — row 1's original
survivor (seed 97/Stow-on-Side) and row 2's (seed 98/Oreburgh City)
traded which entrant they play, confirmed directly against the resolved
pairing (`Wild Area vs Oreburgh City`, `Alfornada vs Stow-on-Side`). The
swap mechanics in `_pa_swap_survivors` were always correct — only
`visiting_of` (survivor assignment) is ever mutated, `entrant_seed_of`
never is — but the log entry itself was built from `entrant_seed_of`
(`my_seed`/`candidate_seed`) instead of the survivor's own seed, so the
report described the opposite of what happened: it looked like the
protected side moved when actually the swappable side did. Fixed by
threading a new `survivor_seed_of: {row_idx: seed}` parameter into
`_pa_swap_survivors` (each row's survivor's own original seed, separate
from `survivor_team_of`) and logging
`survivor_seed_of[visiting_of[row_idx]]` /
`survivor_seed_of[visiting_of[candidate_row_idx]]` (the seeds that
actually trade places) instead of the entrant seeds. No behavior change
— pairings, swap decisions, and game counts are all identical before and
after; only the seed values written into the log entry changed. Row 1's
entry now reads "Swapped seed #97 &harr; #98," matching the real
Stow-on-Side/Oreburgh City exchange.

### Rank/Elo History tab

Added 2026-08-05, mirroring the S9 workbook's own hand-tracked history
(`Rankings!CG:CT` for rank, `Calcs!ANU:AOF` for Elo) instead of copying
those exact columns. `rank_elo_history(season)` in the engine builds both
from `team_round_ratings`/`games` directly, at two checkpoint
granularities, matching the workbook's own two granularities:

- **Elo checkpoints** (Calcs pattern): one per individual event, R/L keep
  their own 1-15 numbering, RT its own matchday numbering, and every
  RDS/PA Draw or Process round gets its own sequential `S` label (`S1`/`S2`
  = RDS round 1 Draw/Process, `S3`/`S4` = RDS round 2 Draw/Process, and so
  on). Elo value at each checkpoint = the team's `elo_a_after`/
  `elo_b_after` from their last game as of that abs_round, carried forward
  for rounds they didn't play in.
- **Rank checkpoints** (Rankings pattern): within the historical portion
  (abs_round &le; the last `_CONFIRMED_ABS_ROUND` value, currently 11) a
  cup round's Draw and Process collapse into one `SC` checkpoint
  (Rankings' SC1/SC2), using the later of the two abs_rounds — matching
  how the S9 workbook actually tracked it. **Per explicit instruction,
  this merging stops there**: every cup round from abs_round 12 onward
  gets its own rank column instead, reusing the exact same `S` label Elo
  assigns that abs_round, so Rank and Elo checkpoints are identical from
  that point on (only the already-published SC1/SC2 differ between the
  two views).

  **Rank values for S8 End/R1/R2/R3/SC1/R4/L1/L2/R5 are copied verbatim
  from `Rankings!CT:CL`** (`rank_history_verbatim.json`, `{checkpoint_label:
  {team_name: rank}}`), **not recomputed** — this engine's OVR-based rank
  doesn't reproduce the workbook's own frozen rank column (see below), and
  per explicit instruction those 9 historical checkpoints should read
  exactly what's in the workbook, byte for byte. `S8 End` (Rankings!CT, the
  season-opening snapshot) leads the list and has no abs_round of its own —
  it's keyed on a sentinel abs_round of 0 internally, never resolved
  through `team_round_ratings`. **SC2 is the deliberate exception**: it's
  the last *historical-labeled* checkpoint but reflects this engine's live
  computed rank (`ORDER BY ovr DESC`, same method `current_rank_lookup()`
  uses for "current" rank everywhere else in the dashboard) — per explicit
  instruction, that's where the new system takes over. Every checkpoint
  from S5 onward is live-computed the same way.

**Columns run newest-first, changed 2026-09-08 (per explicit request).**
The most recent checkpoint now sits immediately beside the sticky Team
column and the oldest (`S8 End` for rank, `R1` for Elo) is at the far
right, so the numbers that actually matter are visible without scrolling
a 26-column table to its end. Only the *column order* is reversed:
`RANK_ELO_HISTORY`'s arrays stay chronological (the engine builds them
that way, and the team sort still reads "latest" as the last element), and
`renderHistory()` walks a `colOrder` index list instead. That distinction
matters for the improved/worsened coloring — `historyCellStyle` still
compares each cell against `hist[i - 1]`, the checkpoint *earlier in
time*, which now renders to the cell's **right** rather than its left.
Verified via Playwright in both modes: headers equal the checkpoint list
reversed, all 160 team rows read their history backwards, and all 4,160
rank cells carry the same color they did before the reversal.

Both walk `full_schedule_abs_round_mapping()` (not `WEEKLY_SCHEDULE`'s
week/day placement) to decide event order and merging, since the
historical portion's actual play order doesn't necessarily match
`WEEKLY_SCHEDULE`'s current day slots — only the confirmed abs_round
numbering does. Grows automatically as `add-results` brings in new
rounds; nothing here needs hand-updating for Elo or for any rank
checkpoint from SC2 onward -- the verbatim file only ever needs the 9
entries it already has.

**Validated against the real workbook**: Elo checkpoints match
`Calcs!ANV:AOF` **exactly**, byte-for-byte, for every team checked
(confirmed against Canalave City's full row: 2506, 2507, 2508, 2509,
2510, 2511, 2534, 2536, 2537, 2539, 2543). The 9 verbatim rank checkpoints
match `Rankings!CT:CL` **exactly** for all 160 teams (1,440/1,440 values).

**Real finding, worth remembering**: this engine's OVR-based rank does
**not** match the workbook's own rank column, not even at the
current/latest state — the *live* `Rankings!AN` "Rank" column (itself an
exact match to the `CK`/SC2 snapshot, 160/160) only agrees with this
engine's current rank on 10/160 teams. Not a bug: the workbook's own `AQ`
"OVR" column for Canalave (103.23) doesn't match this engine's confirmed
value (101.76, Grade SS — the exact number already cross-checked
elsewhere in this file) either. The workbook's Rank/OVR columns are
frozen at whatever formula version was live when they were last touched,
predating several of the confirmed OVR fixes above (PDG denominator,
per-team EX Bonus, TOT floor). This is exactly *why* SC2 onward
deliberately uses this engine's numbers instead of trying to reconcile
with the workbook's stale formula — the two were never going to agree,
and per explicit instruction the new system takes over at SC2 rather than
the workbook's numbers being treated as authoritative going forward.

**Before shipping any dashboard change**: extract the `<script>` block and
actually execute it in Node with a mocked `document` object (not just
confirm it parses) — this caught multiple real bugs tonight (accidentally
deleted helper functions, stale variable references, a `re.sub()` gotcha
where Python silently reinterpreted `\t` in a replacement string as a
literal tab, producing invalid JS) that looked fine on visual inspection
alone. When re-embedding large JSON blocks via regex, use a **lambda**
replacement function, not a raw string — `re.sub()` processes backslash
escapes in string replacements, which corrupts anything containing `\t`/`\n`.

**`SCHEDULE_DATA` (Schedule tab) was a silent gap the same way `PA_CUP_DATA`
once was, found and fixed 2026-08-08.** Reported directly: League round 3
(L3, abs_round 14) results were in the database but never showed up on the
Schedule tab. Cause: `SCHEDULE_DATA` was never wired into
`regenerate_dashboard.py` at all — `grep`-ing the whole repo for the
constant's name turned up only `deckfield_dashboard.html` itself, meaning
it had been hand-baked once by an early one-time script and left static
ever since, exactly the same class of bug as the `PA_CUP_DATA` finding
above ("looks like static seed data" vs. "is actually derived and needs
regenerating"). Fixed with a new `build_schedule_data()`: for each
region/division (from `conf_pods.json`/`league_pods.json`) and each local
Regional/League round 1-15, walks `generate_pod_schedule()` for the
structural home/away pairing (already validated against real Archive host
data — see the Regional/League pod schedule section above), then looks up
that pair's real game (if played) by team-pair alone, since Regional/League
are true single round-robins within their group and each pair meets
exactly once per season. Orientation matters here: `games` doesn't store
which of `team_a`/`team_b` hosted directly (only `host_region`, which can't
disambiguate a Regional game since both teams already share a region), so
the pod schedule's own home/away call is treated as ground truth and the
score is oriented onto it by checking which team_id the game's `team_a`
actually was, not by trusting a stored "home" flag. Wired into `main()`
right after `CALENDAR_DATA`. Verified: League division 1 round 3 (`Castelia
City` 19 vs `Canalave City` 56, etc.) now carries real scores where it
previously had none; Regional round 12 (genuinely unplayed) still correctly
carries only the structural pairing, no score keys — confirming the fix
adds real data without inventing any.

**Winner/team-name coloring extended to the Rankings tab, and Regional
Standings gained region-colored Top-4/Top-8 divider lines, 2026-08-08 (both
per explicit request).** Rankings' team-name cell now reuses the same
`winnerNameHtml()` helper the Rank/Elo History tab already used (colors a
team's name by their own region, falling back to a plain highlight color if
the team can't be resolved) — previously plain text, `${t.name}` with no
color at all. Regional Standings (16 teams/region) gets a 3px
region-colored `border-bottom` after seed 4 and after seed 8, no text
label — the natural breakpoints toward the Regional Tournament (see that
section above): seeds 1-4 bye all the way to MD4/5, seeds 5-8 bye to MD2/3,
seeds 9-16 start at MD1. New `standingsDividerStyle(mode, groupVal, i)`
gates on `mode === 'region'` so League (division) standings — which have
no equivalent tournament-bye structure, only the existing promotion/
relegation markers — never get a divider. Applied as an inline style on
every `<td>` in the row rather than the `<tr>` itself, since the
`table { border-collapse: separate }` rule already in use means a
`border-bottom` set on `<tr>` doesn't reliably render — it has to go on
the cells. Verified via Playwright: Indigo's Regional Standings shows the
divider in Indigo's own bright red (`#E4574A`) after seeds 4 and 8 only,
all other rows unstyled, and League (division) standings render with no
divider styling at all.

**RP/LP dropped as a standings tiebreaker, 2026-09-08 (per explicit
instruction).** Regional/League Standings previously sorted W-L, then
RP/LP, then H2H, then DSCR, and showed an RP/LP column. Points are now out
of the standings entirely — both as a tiebreaker and as a displayed column
— leaving **W-L, then H2H, then the respective DSCR**. RP/LP themselves
are untouched everywhere else (they still feed `TOT`, the Rankings tab and
`_points_buckets()`); this is only about what the standings leaderboards
sort and show.

**As of 2026-09-10 the Python side lives in one place**: the sort was
extracted into `_standings_order(season, teams, game_type)`, with
`regional_standings_seeds()` (game_type `R`) and the new
`division_standings_seeds()` (game_type `L`, added for the World
Championship's division bids) as thin wrappers. Both are true single
round-robins within their group, which is what makes one shared sort
correct for both -- and it means a future tiebreak change can no longer
be applied to regions but forgotten for divisions. The JS is still a
separate port; that pair still has to be kept in step by hand. The
refactor was verified byte-for-byte: `RT_DATA` and every other derived
constant regenerated identically.

The change has to be made in **two places that must agree**:
`renderStandings()`/`standingsOrder()` in the dashboard JS, and
`regional_standings_seeds()` in the engine — the latter is a deliberate
port of the former (see the Regional Tournament section) and feeds
`RT_DATA`, so leaving it on the old sort would have silently seeded the
postseason off different standings than the tab displays. Both dropped
`pointsOf`/`rp` from the sort key *and* from the tie-group boundary (the
`while` that decides which teams form a tied group at all) — the second is
easy to miss and is what actually widens the groups H2H/DSCR now resolve.

**Not a no-op:** removing RP moved **63 of 160 seed slots**, in all 10
regions (e.g. Indigo's seed 9 went Celadon City → Lavender Town, Kalosite
and Phoenix moved 10 slots each), because RP was previously splitting
teams into separate one-team groups that H2H/DSCR never got to compare.
Verified end to end: `regional_standings_seeds()` and the tab's
`standingsOrder()` return **identical orders for all 160 teams across all
10 regions**, all 40 regenerated `RT_DATA` MD1 slots match the new
seeding, the seed-4/seed-8 region-colored dividers still land on every
`<td>` of those rows (one cell narrower now), and League standings keep
their P/R markers.

**The RDS Cup tab gained a Mutual Stage section, 2026-09-11.** The shared
semifinal/final belongs to neither Draw nor Process, so it renders as its
own full-width section rather than being duplicated into both bracket
tables. `RDS_MUTUAL_STAGE` (derived, in the manifest) carries each cup's
pairing in leg-2 orientation with both seeds, the bye team where there is
one, `resolve_mutual_stage`'s own explanation, and any real leg results. A
cup whose brackets have not both finished round 5 is simply absent from it.

**It is per-cup and sits at the TOP of the panel, corrected the same day
per explicit instruction.** The first version rendered one shared table at
the *bottom* of `#panel-rds` listing all three cups stacked -- so the
identical block appeared under every cup, since the `#rds-cup-toggle`
handler only called `renderRdsCup()`. Each cup now gets its own block,
above the Draw/Process columns, showing only `RDS_MUTUAL_STAGE[rdsCup]`.
Three things this needs that are easy to miss:

- `#rds-mutual-section` wraps the heading *and* the table, and
  `renderRdsMutual()` toggles `section.hidden` -- hiding only the table
  would leave an orphan "Mutual Stage" heading for a cup that has not
  reached it.
- The cup toggle handler has to call `renderRdsMutual()` alongside
  `renderRdsCup()`, or switching cups leaves the previous cup's stage on
  screen.
- The "Awaiting the semifinal." placeholder uses `qual-sub`, not
  `sb-round`: `.sb-round` is `text-transform: uppercase`, which shouted it.

That uppercase rule also produced a **false test failure** -- a check for
the literal string `Ribbon` in the rendered rows fails against the rendered
`RIBBON CUP`. Exactly the text-matching trap the runbook warns about;
compare case-insensitively or query the DOM. Verified via Playwright:
section renders before `.schedule-columns` in `#panel-rds`, each cup's
toggle swaps the block (Ribbon and Star one SF + a bye row, Dream two SFs
and no bye), a cup deleted from the constant hides the section to zero
height while its Draw/Process tables still render, and all 11 tabs load
with zero `pageerror` events.

That panel's `meta-note` was badly stale and was rewritten at the same
time: it claimed real results existed only for rounds 1-2 and that "rounds
4-5 aren't shown yet -- no results exist for round 3 in this database yet",
when rounds 3, 4 and 5 have all since been played.

**The Mutual Stage box was full-panel width and is now bracket width,
2026-09-12 (per explicit request).** The Draw/Process boxes are
content-width — `.schedule-columns` is a flex row whose items are bare
`<div>`s, and the `flex: 1` rule targets `.table-scroll` *inside* them, so
it never applies — leaving them about 985-1045px of a 1536px panel at
1600px viewport. The Mutual Stage box meanwhile filled the panel with its
table centred by `table.scoreboard { margin: 0 auto }`, so it floated
across dead space the brackets never used.

Fixed with a `.rds-stack` wrapper around the mutual section and the
brackets: `width: fit-content` makes it take the widest block inside it
(the two brackets side by side) and `.rds-stack > * { width: 100% }` makes
every child fill exactly that. Two things are load-bearing and neither is
obvious:

- **The panel's `meta-note` must stay OUT of the wrapper.** A paragraph's
  max-content is one unwrapped line, so including it sized the wrapper
  straight back to the full 1536px. That was the first attempt, and it
  looked like `fit-content` simply not working.
- **`#rds-mutual-section { contain: inline-size }`** keeps the mutual box
  out of the wrapper's intrinsic-width calculation, so the brackets alone
  decide it and a long resolution note can never widen the panel. Without
  it Dream's box (the longest note) overran the brackets at ≤800px.

Verified via Playwright: the mutual section, `.schedule-columns` and the
wrapper come out at identical widths for all three cups (985 / 1002 /
1045px against a 1536px panel), the table fills its box less the 1px
scroll-box border each side, and all 11 tabs load with zero `pageerror`
events. Note the page already scrolls horizontally below ~1400px — that
predates this change (confirmed against main) and is untouched.

**Standings gained an overall-rank column, 2026-09-11 (per explicit
request).** Each Standings box now shows a team's league-wide OVR rank
(1-160) between its own standings position and its name, so a region's
leader can be read against the other 159 teams without leaving the tab --
Indigo's first-placed Cerulean Cape currently sits 86th overall, which the
box alone never showed.

Purely a renderer change: `t.rank` is already on every `DATA.teams` entry,
so nothing new is derived and `regenerate_dashboard.py` was deliberately
NOT re-run (it would have rewritten every constant from whatever the local
database held, which is a different question from this change).

The header is **"Rank"** because both obvious names were already taken and
mean something else here: `#` is that box's own standings position, and
the Rankings tab uses "Overall" for the OVR *score*, not a placing. A
`title` on the `th` spells out that it is the OVR ranking across all 160.
The cell carries `style="${divider}"` like every other cell in the row --
the seed-4/seed-8 region dividers are applied per-`<td>` (see the divider
entry above), so a new column that omitted it would have left a visible
gap in the line.

**`CUP_REAL_RESULTS`/`RDS_ROUND2`/`RDS_ROUND3` (RDS Cup tab) were the same
kind of silent gap as `PA_CUP_DATA`/`SCHEDULE_DATA`, found and fixed
2026-08-08.** Reported directly: RDS Cup round 3 (Draw = abs_round 15,
Process = abs_round 16) was complete in the database, but the RDS Cup tab
still showed round 3 as an unscored structural preview and had no round 4
at all. Cause: `CUP_REAL_RESULTS` only ever covered rounds 1-2, and
`RDS_ROUND2`/`RDS_ROUND3` were each a one-shot hand-baked snapshot for one
specific round — none of the three were ever wired into
`regenerate_dashboard.py`, so nothing advanced them as later rounds got
played. This diagnosis in the earlier `SCHEDULE_DATA` entry above turned
out to be wrong about these three specifically: they were listed as
"genuinely unaffected by Regional/League/PA results" and fine to leave
alone until an RDS round actually came in — but no fix ever landed once
one did.

Fixed with two new functions and a JS renderer generalization, not just a
one-off patch for round 3→4:
- `rds_cup_real_results(season)` — generalizes the old rounds-1-2-only
  `CUP_REAL_RESULTS` to every round that has real games so far, across
  all three cups, mirroring `pa_cup_real_results()`.
- `rds_cup_round_pairings(season)` / `_rds_cup_round_pairings()` —
  replaces the one-shot `RDS_ROUND2`/`RDS_ROUND3` snapshots with a single
  `RDS_ROUND_PAIRINGS` constant covering every round 2-5 whose pairing is
  resolvable (every prior round complete), recomputed fresh every run via
  `_rds_round_games()`. This naturally covers every round already played
  *and* exactly one round ahead (the "what's next" preview) — a round
  stops appearing in the output the moment the round before it isn't
  complete yet, so the accumulation is self-limiting without any special
  round-number bookkeeping. Round 1 stays out of this (real, fixed seed
  data in the already-static `CUP_BRACKET_DATA`, never changes). Never
  resolves round 6 (the mutual semifinal/final stage) — that's a
  different resolution mechanism entirely (`resolve_mutual_stage`), not
  naive further bracketing, and isn't wired into a playable event yet.
- `renderRdsCup()` (dashboard JS) rewritten from two hardcoded
  round-2/round-3 blocks into a `for (rnd = 2; rnd <= 5; rnd++)` loop over
  `RDS_ROUND_PAIRINGS`: a round with real results renders via
  `rdsRound1RowHtml` (winner-colored, scored, same as Round 1); a round
  with pairing but no results yet renders via `rdsRound3RowHtml` (dash
  score, structural only) and the loop breaks, since only one round is
  ever shown ahead of what's been played. This is why the round-2/round-3
  special-casing had to go, not just get a round-4 case bolted on: a
  hardcoded per-round block would need a new special case forever, one
  RDS round at a time, exactly the maintenance trap the earlier
  `PA_ROUND_PREVIEW` design was already built to avoid.

Verified via Playwright across all six brackets (Ribbon/Dream/Star ×
Draw/Process): each now shows Round 1-4 sections (newest first), Round 3
rendering with real winner-colored scores (e.g. Ribbon Draw's Cherrygrove
City 43-26 over Vermilion City), and Round 4 rendering as an unscored
preview pairing (Cherrygrove City vs Mount Silver, the round-3 winners).

**`PA_ROUND_PREVIEW` turned out to have a related but distinct bug of its
own, found and fixed 2026-08-08, same day.** The RDS Cup fix above
specifically called out `PA_ROUND_PREVIEW`'s single-round-preview design
as the thing "already built to avoid" the maintenance trap of hardcoded
per-round blocks — that part held up, but a second, separate flaw in the
same design surfaced for real the same day: PA Draw round 2 was played
(no Process round 2 yet), and the PA Cup tab's pairings **disappeared
entirely for both brackets**, not just failed to advance. Cause:
`build_pa_cup()`'s preview target was `(max round with ANY real results
in EITHER bracket) + 1` — a single shared target assuming Draw and
Process stay in lockstep. They don't: Draw always plays before Process
each PA matchday (Tue vs Thu). The moment Draw round 2 had real results,
target jumped straight to round 3 — but round 3's pairing needs BOTH
brackets to finish round 2 first (the cross-bracket duplicate check
needs both), so `pa_cup_round_preview(season, 3)` returned `None` for
both brackets, and the previously-populated round-2 preview (seed/
home-away info, which `PA_REAL_RESULTS` never carries — see
`pa_cup_real_results`'s own docstring) was gone, taking down Draw's
now-real round-2 results display along with Process's still-valid
round-2 upcoming preview.

Fixed the same way as the RDS Cup fix immediately above it: replaced the
single `PA_ROUND_PREVIEW` snapshot with an accumulating
`PA_ROUND_PAIRINGS` (`{round_str: {"Draw": [...], "Process": [...]}}`
for every round 2-7 whose pairing is actually resolvable — both brackets
complete on the round before it), computed fresh every run in
`build_pa_cup()` by walking `pa_cup_round_preview()` round by round and
stopping at the first one that returns `None`. Each bracket's renderer
then decides independently, via `PA_REAL_RESULTS`, whether a given
round counts as "played" for *that* bracket — so Draw round 2 renders
scored/colored while Process round 2 renders as the correct unscored
preview, from the exact same shared pairing data. Conflict-resolution
log extracted into its own `PA_SWAP_LOG` constant (whichever round
resolved highest, capped at round 4 — rounds 5-7 never get conflict
resolution): `pa_cup_round_preview()`'s swap_log for a given target
round already self-accumulates every round from 2 up to that target via
`_pa_ladder_walk`, so concatenating swap_logs across multiple stored
`PA_ROUND_PAIRINGS` rounds would double-count — using only the single
highest-resolved round's own swap_log is both correct and complete.

Verified via Playwright: PA Draw's tab shows Round 1 + Round 2 with
Round 2 real, scored, and winner-colored (Wild Area 42-25 over Oreburgh
City); PA Process's tab shows Round 1 + Round 2 with Round 2 correctly
unscored (still upcoming); the Conflict Resolution Log shows all 24
entries (10 from round 1, 14 from round 2 — matching the count already
confirmed in the 2026-08-07 conflict-log entry above).

## deckfield.html Spread calculation

**Fatigue Multiplier applied in reverse for an away-favored spread, fixed
2026-08-09 (per explicit correction).** `computeSpread()`'s
`fatigueMult = 1 + (TEAM_FATIGUE.away - TEAM_FATIGUE.home) * 0.005` was
already fixed once before (commit `aa6bb0a`, "Fix Fatigue Multiplier
applying in reverse") to get the home-favored case right: a more-fatigued
home team correctly yields a multiplier below 1, shrinking `rawSpread` in
the tired favorite's disfavor. But that formula's sign only actually
tracks home-vs-away, not favorite-vs-underdog — it silently assumed
`rawSpread` is always positive (home favored). When `rawSpread` is
negative (away favored) and the away side — the favorite — is also the
more-fatigued team, the same formula still produces a multiplier above 1
(since away fatigue exceeds home fatigue), and multiplying a *negative*
number by something above 1 makes it more negative, i.e. **grows** the
tired favorite's margin instead of shrinking it — backwards, and the
mirror image of the bug `aa6bb0a` already fixed once for the home side.

Fixed by folding `rawSpread`'s own sign into the fatigue term:
`fatigueMult = 1 + sign(rawSpread) * (TEAM_FATIGUE.away - TEAM_FATIGUE.home) * 0.005`.
This ties the multiplier's effect to favorite/underdog instead of
home/away: it shrinks whichever side's margin is favored when that
favorite is the more-tired team, and grows it when the *underdog* is the
more-tired team — symmetric in both directions. When `rawSpread > 0`
(home favored), `sign` is `+1` and the formula is byte-identical to the
already-verified `aa6bb0a` fix, so that case is unchanged.

Verified via Playwright with two controlled two-team rosters (skill
rating driving who's favored, fatigue set directly per team) loaded
through the real roster/schedule import + Load Next Match flow, reading
the actual Game Spread Calculation panel:
- Home favored (raw spread +8.292), home fatigue 90 vs away fatigue 10
  (favorite is the tired team): Fatigue Multiplier **0.600**, spread
  shrinks from 8.292 to 4.9751 — matches `aa6bb0a`'s original verified
  case exactly, confirming no regression.
- Away favored (raw spread −3.708), away fatigue 90 vs home fatigue 10
  (favorite is the tired team, mirrored to the away side): Fatigue
  Multiplier **0.600** (previously would have been 1.400, growing the
  away favorite's margin instead of shrinking it), final spread AWAY
  Team +2.2248 instead of the pre-fix +5.19 that the reversed multiplier
  would have produced.

**Factor Multiplier, added 2026-09-11 (per explicit request).**
`SPREAD_FACTOR_MULTIPLIER` had sat at a hardcoded `1` since the file was
written ("placeholder for future conditional multiplier"). It is now a real
input, and it **always favours the home team** -- deliberately:

```
subtotal = rawSpread * fatigueMult
spread   = (subtotal >= 0) ? subtotal * M : subtotal / M
spread  += SPREAD_FACTOR_MODIFIER
```

Home favoured, so multiply and home's margin grows; away favoured, so
divide and away's margin shrinks. Either way an M above 1 helps home.

**This is the opposite convention from the Fatigue Multiplier one line
above it, and that is intentional** -- the fatigue rule was specifically
fixed (see the Spread section above) to be favourite/underdog-symmetric so
it never cares which side is home, while this one is home-biased on
purpose. The call site says so in a comment, because the obvious "cleanup"
is to make them match, and that would silently delete the home factor.

Where it lives: a manual input in **Schedule Batch Settings**, beside
Starting Timeslot and Games per Timeslot. Those two were already the only
fields the pasted Batch Settings row does not fill, and this is a third of
the same kind -- `parseBatchSettingsPaste()` only ever writes the 8
`BATCH_SETTINGS_COLUMNS`, so a new manual field needs **no engine change
and no change to the pasted row's strict 8-column check**. It is read and
clamped once at parse time and stamped onto every match as `factorMult`
(exactly how `timeslot` is derived from the two timeslot fields), then
loaded per match in `loadScheduledMatch()` next to its sibling
`SPREAD_FACTOR_MODIFIER` (`Adv`).

Worth knowing if this ever needs to vary **within** a matchday: its
sibling `Adv` is already per-game (column 3 of every Matchups row), so the
two halves of the same formula step currently arrive at different
granularities. Batch level is right while a multiplier is a property of
the occasion, which is what the Elo coupling below implies. If it ever
needs to be per-matchup, the pattern is already proven in this file --
per-row Cup Name overriding batch-level Cup Name -- so add a 4th Matchups
column that wins when present and falls back to the batch field otherwise.

Three details that matter:
- **Clamped to 1-2 on parse**, not trusted from the field. Below 1 would
  invert the always-favours-home effect, and 0 would divide an
  away-favoured subtotal to `Infinity`.
- **The Elo K bonus is now live.** `computeEloUpdate()`'s
  `(SPREAD_FACTOR_MULTIPLIER > 1.0) ? 8 : 0` had never once fired while the
  value was pinned at 1; any batch above 1 now adds 8 to K for every game
  in it. Confirmed intended.
- **The breakdown row flips its own operator**: `renderSpreadBreakdown()`
  renders `&divide; Factor Multiplier` when `factorDivides`, otherwise
  `&times;`. Without that the panel misstates the arithmetic it is showing.

No Results CSV change was needed: the multiplier lands in the spread that
`spread_a` already exports, so the 21-column `CSV_GAME_FIELDS` contract is
untouched (verified -- an away-favoured M=2 game exported `spread_a
-3.8540`, the divided value).

Verified via Playwright through the real roster/schedule import, reading
the rendered Game Spread Calculation panel (the whole script is inside an
IIFE, so internals are unreachable from `page.evaluate` -- read the DOM):
home favoured went 12.2919 -> 24.5839 at M=2 (x2, margin grew) and away
favoured went -7.7080 -> -3.8540 (/2, margin shrank), the row label read
`x` then `&divide;` respectively, inputs 0/-5/5/1.5/blank clamped to
1/1/2/1.5/1, and the Elo panel showed `K: multiplier bonus` 8 at M=2 and 0
at M=1.

**Two stale instructions in that panel's own help text were corrected at
the same time.** It claimed the pasted row fills everything "except
Starting Timeslot and Games per Timeslot" (now three fields), and -- this
one stale since 2026-08-07 -- that "RDS Cup matchdays give you three
separate rows (Ribbon/Dream/Star); apply and import each one as its own
batch", which the single-combined-batch change replaced long ago. The UI
was still telling the reader to do the old three-batch workflow.

## deckfield.html layout (three columns + inline tabs)

**Reformed 2026-09-03 (per explicit request).** The page used to stack
brand → scoreboard → tab bar → a two-column Field layout (pitch | cards
over log). It's now:

1. `.topbar` — the DECKFIELD wordmark and the tab bar share one line, with
   the tab bar right-aligned. The tab bar lost its own `margin-bottom`/
   `border-bottom` and gained `margin-bottom:-1px`, so the active tab's gold
   underline sits directly *on* `.topbar`'s rule instead of drawing a second
   one a few pixels below it.
2. The score banner (`.scoreboard-row`) sits under that, still inside
   `<header>` — deliberately outside the tab contents, so it stays put when
   switching tabs, exactly as before.
3. `.layout` is now **three** columns — `finals | pitch | cards/log` — with
   `.finals-panel` spanning both grid rows down the left edge. `.wrap`'s
   `max-width` went 1040px → 1560px to pay for the extra column.

Reflow: at ≤1240px it drops back to the original two columns with the
finals panel moving full-width *below* them (it's the least
attention-critical of the four panels); at ≤800px everything is one column,
finals last.

**Column balance and pitch legibility, adjusted 2026-09-03 (per explicit
request).** Grid columns went `0.62fr / 1.9fr / 1fr` →
`0.85fr / 1.65fr / 1fr`: the finals column gains the width back from the
pitch (at 1600px, 269px → 371px for finals, 825px → 720px for the pitch),
since long team names were the thing being cramped. The pitch's zone
numbers went 10px → 12px (and slightly less transparent, 0.6 → 0.72) to
stay readable in the narrower cells. No overflow risk at any ATK modifier:
`ATK_TABLE` values are always 1-9, so a cell's label is at most three
characters (`6/6`) — about 22px of text in a 42px cell.

**The column's own type was scaled too, 2026-09-03 (per explicit
correction).** Widening the column alone just turned the extra 100px into
whitespace — the finals text was still at the sizes picked for the old
narrow column. Bumped alongside it: `.finals-body` 11 → 12.5px (team
names), `.fr-score` 16 → 19px, `.fr-rank` 10 → 11px, `.fc-top` 9 → 10px,
`.finals-count` 11 → 12px, with card padding 6/8 → 7/9px to match. Checked
for truncation with the longest real team names (`City of Circhester`,
`Glaseado Mountain`, `Vast Poni Canyon`) at 1241px — the narrowest the
three-column layout ever gets before the panel reflows full-width — plus
1400 and 1920px: zero names ellipsised and zero rows overflowing at any of
them. The lesson generalises: "make it bigger for readability" means the
type, not only the box.

**The ball marker is a direction arrow, not a ball (2026-09-03, per
explicit request).** `.ball` is now a clip-path arrow that points the way
the ball is about to travel, mirrored via `.point-left`, set in
`renderBallAndTrail()` from `game.turnTeam`.

- **Only the horizontal component is shown, deliberately.**
  `Game.processTurn` uses `dir = team === 'home' ? -1 : 1` — home always
  drives left, away always drives right — and `turnTeam` is settled before
  the move card is drawn, so that direction is genuinely known in advance.
  The vertical step is `lateralDelta(c1.suit)`, which isn't known until the
  card is dealt; an arrow claiming a diagonal would be inventing it.
- **`turnTeam` really is the *next* mover**, including the two cases that
  aren't simple alternation: `if (!scored) this.turnTeam = otherTeam(team)`
  leaves possession with the scorer (who kicks off next), and
  `startPeriod()` reassigns it at every period change.
- **`filter: drop-shadow`, not `box-shadow`** — `clip-path` clips a
  box-shadow away entirely, while a filter follows the clipped silhouette.
- **`transform` is excluded from the transition** on purpose: animating the
  mirror would squash the arrow through zero width on every change of
  possession.
- The three trail markers stay small circles — they're where the ball has
  been, which has no direction to show.

Verified over 40 consecutive turns that the arrow's predicted direction
matches the team that actually moved next, 40/40, post-goal kickoffs
included. Worth recording how that check first went wrong: the test read
the newest play-by-play line matching `[TEAM]`, but a goal logs as
`★ GOAL <TEAM>!` — so on scoring turns it silently compared against the
*previous* turn's mover and reported three phantom mismatches. The fix was
to scan only the lines that turn added and to recognise all three log
shapes (move, kickoff, goal).

**The Final Scores column** (`renderFinals()` and friends, next to the
Results-log section) mirrors the currently-loaded `SCHEDULE` one card per
matchup, filling each in as its game ends — so an Auto-Play Week reads as a
live matchday scoreboard instead of only landing in the Results CSV.
Details worth remembering:

- Keyed on the **SCHEDULE index** (`MATCH_FINALS`), so replaying a match
  overwrites its own card rather than adding a second one. A game played
  with no schedule entry loaded (the built-in placeholder match) goes to
  `UNSCHEDULED_FINALS`.
- **Ordered newest-first, like the play-by-play log** (2026-09-03, per
  explicit request). Top to bottom: the match currently in progress, then
  every final in *completion* order newest-first, then the matchups still
  to come in schedule order. `scrollFinalsToTop()` snaps the column back to
  the top whenever something lands there, exactly as `appendLog()` does
  after prepending a play-by-play line. Completion order needs its own
  `finalsSeq` counter — `MATCH_FINALS` is keyed on schedule index, which
  says nothing about *when* a game was played, so a replayed match has to
  be able to jump back to the top. The in-progress card deliberately sits
  *above* the newest final: it's about to become the newest final, so
  holding the top slot means finishing it fills the card in place instead
  of making the list jump.
- `buildFinalRecord()` **snapshots** the team names/ranks/regions/round at
  the moment the game ends. It can't read them back later:
  `loadScheduledMatch()` overwrites `TEAM_DISPLAY_NAME`/`TEAM_REGION`/
  `RANK_*`/`CUP_NAME` wholesale for the next match.
- Hooked into `recordResultIfNeeded()`, the single funnel every play path
  already goes through (manual Deal, Sim Game, Auto-Play, Auto-Play Week's
  replay) because it's called from `renderAll()`. Nothing per-path needed
  wiring, and Auto-Play Week was verified end to end regardless.
- Winners are colored by **their own region** via `regionBright()` —
  the same convention the dashboard's `winnerNameHtml()` already uses, not
  a fixed home/away color.
- Re-parsing a schedule (or clearing it) calls `clearFinals()` and resets
  `currentMatchIndex`: a new matchday means the old indices no longer point
  at the same games, so keeping the cards would silently mislabel them.
- `escHtml()` was added here (there was no escaping helper in the file) —
  team names come from pasted spreadsheet data.

Verified via Playwright at 1600/900/700px: brand and tabs share a line,
scoreboard sits below them, finals is leftmost and spans both rows, cards
and log share column 3, pending → in-progress → final card states all
render, the winner's name carries their region's bright hex, the Results
CSV is unaffected, re-parsing clears the column, and Auto-Play Week fills
it 2/2 with no page errors.

## Auto-Play Week snapshots (deckfield.html)

**`simulateFullyWithHistory()` no longer clones the log per turn, fixed
2026-09-03.** Every turn's snapshot was `structuredClone(game)`, and
`game.log` is append-only — so each snapshot re-copied an ever-growing
array and the whole hidden simulation was quadratic in turn count. Measured
in a faithful reproduction of the loop at ~320 turns (a typical full game):
**82ms and ~103k log-entry copies per game**, versus **15ms** with the log
excluded — roughly 6.6s → 1.2s across an 80-game matchday.

The fix keeps `log` out of each snapshot, stores a `logLength` marker
instead, and hangs the one complete log off the returned history as
`fullLog`. New `restoreSnapshot(history, index)` is the only way a snapshot
goes back onto `game`: `Object.assign` alone would leave `game.log` at
whatever it currently holds (the snapshots carry none), so the right prefix
has to be sliced back on explicitly — `flushHistoryTo()` and
`replayHistory()` both route through it, and `replayHistory`'s `prevLogLen`
now reads `snap.logLength` rather than `snap.log.length`. **`deck` is still
cloned** — it's a bounded 52 entries, not the quadratic term, and leaving it
in keeps the snapshot a faithful game state.

Verified in the real page, not just in the benchmark: the `flushHistoryTo`
path (tier-3 "sim") renders the full 159-line log of a completed game, and
the `replayHistory` path grows the log turn by turn (6 → 8 → 10 → 14 → 16
lines across five samples) instead of dumping the whole game on the first
step — which is exactly what a wrong prefix slice would have produced. No
page errors on either path.

## Known open items

- Dashboard regeneration isn't in the CLI yet — still manual script runs.
- Round numbering: the *historical* R1-R5/L1-L2 mapping is confirmed
  against real Archive data; everything from PA Draw round 1 onward uses
  `abs_round_for_event()`'s sequential assignment, which is only "confirmed
  right" in the sense that it's internally consistent, not independently
  cross-checked against a second source the way the historical portion was.
- **RDS Cup's mutual semifinal/final is wired as of 2026-09-11** (see the
  RDS Cup section). **PA Cup's mutual semifinal/final is not** —
  `_games_for_event` still raises `NotImplementedError` for it
  deliberately rather than guessing. Since the quarterfinal was dropped
  (2026-09-12, above), PA's end stage is now structurally **identical** to
  RDS's — 2 per bracket, same duplicate-handling, two-legged SF then the
  final — so `rds_mutual_stage()` / `resolve_mutual_stage()` /
  `_rds_leg_orientation()` are not just a template but very nearly the
  implementation; the differences left are PA's seeding source (a team
  holds a different seed in each bracket, so "better seed" needs deciding)
  and its best-of-three final (week 23's three slots, not two legs).
- DECKFIELD's Results tab now exports directly in the `add-results` CSV
  format (comma-separated, header row, `CSV_GAME_FIELDS` order + `ex_a`/
  `ex_b`/`cup_name`/`cup_bracket`/`cup_round`) instead of the old
  packed-string Excel Archive format — round-trip verified end to end
  (Playwright-driven game → exported row → `deckfield add-results` →
  ingested with no errors, 2026-07-30). `team_a` is always the home team,
  `team_b` always away (`home` is therefore always `"A"`). The Schedule
  tab gained a **Round #** field (the abs_round integer, separate from the
  existing free-text Round Label) and Cup Name/Bracket/Round # fields
  (only populated into the export when Game Type is Cup) — these must be
  set before playing a match for that game's exported row to carry them.
- `deckfield.html` is now the version of record in this project (see top of
  file) — don't let a separate conversation's copy drift back into being
  treated as authoritative.
- **Schedule Batch Settings copy/paste, added 2026-08-05**: `deckfield.html`'s
  Schedule tab can now fill in its Batch Settings (Game Type, Format, Day,
  Round Label, Round #, Cup Name, Cup Bracket, Cup Round #) from a single
  pasted row instead of setting each field by hand — only Starting Timeslot
  and Games per Timeslot stay manual (they're display/pacing choices, not
  derivable from the schedule). Source is `export_matchday_batches()` in
  the engine, surfaced in the dashboard's Next Matchday tab. Format is AGG
  for any two-legged/Bo3 slot (RDS SF/Final, PA QF/SF/Final, Regional
  Tournament MD2 onward — MD1 is the only single-game RT matchday),
  Single Game otherwise.

  **Always exactly one combined batch now, fixed 2026-08-07 (per explicit
  instruction — twice, same day: first added a same-page "combined
  listing" alongside three separate per-cup batches, then corrected to a
  single real batch after "This will not do — I need one combined input
  and one combined output").** RDS Cup matchdays play Ribbon, Dream, and
  Star simultaneously; the original design split them into three separate
  DECKFIELD batches because a single batch's Cup Name setting can only
  hold one value for the whole pasted list. That's real, but it made the
  workflow genuinely painful: three rounds of paste-settings → paste-
  matchups → play 8 games → repeat, instead of one round covering all 24.

  Fixed by moving Cup Name off the batch level for a combined RDS batch and
  onto each matchup row instead: `export_matchday_batches()`'s Matchups
  format grows two optional trailing columns, `Cup Name` and `Cup Bracket`
  (`SCHEDULE_COLUMNS_WITH_CUP` in `deckfield.html`) — present only when a
  batch mixes cups. `deckfield.html`'s Schedule importer accepts either
  3-column (unchanged, single cup or none) or 5-column rows; a row's own
  Cup Name/Bracket wins when present, falling back to the batch-level
  Batch Settings otherwise — so `loadScheduledMatch()` needed **no changes
  at all**, since it already read `match.cupName`/`match.cupBracket` off
  whatever `SCHEDULE` construction resolved. Cup Bracket (Draw/Process)
  stays at the batch level even for a combined RDS batch, since all three
  cups always share the same bracket that matchday — only Cup Name
  actually varies per row. `export_matchday_batches()` now always returns
  a single-element `batches` list (kept as a list for the existing call
  signature) — an RDS batch's `settings_tsv` leaves Cup Name blank (the
  UI's existing `<option value="">—</option>` already handles that
  correctly) and its `games`/`matchups_tsv` carry a `cup` field/columns
  per row; every other event kind (R/L/PA/RT) is completely unchanged,
  still 3-column, still one cup (or none) for the whole batch.

  This made the short-lived "combined listing" feature (a separate
  read-only preview table merging the three batches) redundant — removed
  it entirely, since the single real batch's own Matchups table already
  shows every game in play order with a Cup column when relevant.
  `regenerate_dashboard.py`'s `build_next_matchday()` simplified from
  `"batches": [...]` (a list, sized 1-3) to a single `"batch": {...}`, and
  the dashboard JS no longer needs per-batch-index DOM lookups
  (`data-batch="${idx}"`) since there's only ever one Batch Settings box,
  one Matchups box, and one Region Climate box now.

  Verified end to end: parsed a real 160-team roster, pasted the engine's
  actual generated Batch Settings (Cup Name blank, Cup Bracket "Draw") and
  24-row combined Matchups (Ribbon/Star/Ribbon/Dream/... interleaved by
  rank, not grouped by cup) into `deckfield.html`'s real importer;
  confirmed the Schedule table's per-row Cup column shows the correct cup
  for each of the first 6 rows; loaded row 2 (a Star game) and simulated
  it; confirmed the Results CSV correctly recorded `cup_name=Star,
  cup_bracket=Draw, cup_round=3` for that specific game. Also confirmed a
  PA Cup matchday (always single-cup) is completely unaffected: 3-column
  matchups, batch-level Cup Name still populated, no per-row `cup` key.
