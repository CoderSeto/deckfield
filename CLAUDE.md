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
  Commands: `migrate`, `add-results <csv>`, `recompute --from N`, `status`,
  `next-matchday [--output file]`, `export-teams [--output file]`,
  `region-climate [--output file]`.
- `deckfield.db` — the SQLite database (not checked in; regenerate via
  `deckfield migrate`).
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

## Adding new game results

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
generated live), PA Cup rounds 1-7 (same). RDS's mutual semifinal/final and
PA's mutual quarterfinal/semifinal/final raise `NotImplementedError`
deliberately — not modeled yet, see "Known open items."

## Ratings formulas (validated against real S9 data)

- **RW/LW**: exact match, 160/160 teams. Full formula in
  `deckfield_ranking_spec.md`-equivalent docstrings in the engine.
- **OVR** = weighted blend of 12 components (RW, LW, PF, PA, PDG, SOS, SOV,
  DSCR, EYE, Elo, Cups, EX), each normalized 0–100 (except RW/LW, which can
  exceed 100). PDG's denominator is `avg_raw_goal_score` (the `^X.XX` field
  in the packed string), **not** PF/games_played — this was a real early
  bug.
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
- After **5 rounds** each, Draw and Process reach 2 remaining teams, which
  combine into a shared final stage:
  - 4 distinct finalists: Draw pair plays a semifinal, Process pair plays
    a semifinal, winners meet in the final.
  - 3 distinct (1 team reached the final 2 on both sides): that team byes
    to the final; the other two play the lone semifinal.
  - 2 distinct (same pair both sides): no semifinal, straight to final.
  - Home team for the semifinal/final: whichever team has the better
    original seed (a separate rule from either bracket's own convention).
  - **Not yet built**: the mutual semifinal/final stage itself isn't
    modeled in `_games_for_event` (raises `NotImplementedError`) — the
    resolution *logic* above is confirmed correct, just not wired up as a
    playable event yet.
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
independent ladder rows, row *i* = seeds `(128+i, 96+i, 64+i, 32+i, i)`:

- Round 1: `(128+i) vs (96+i)` — both always tier D (seeds 97–160, no bye).
- Round 2 winner faces `64+i` (tier C, seeds 65–96, bye to round 2).
- Round 3 winner faces `32+i` (tier B, seeds 33–64, bye to round 3).
- Round 4 winner faces `i` (tier A, seeds 1–32, bye to round 4).
- Rounds 5–7: standard bracket among the 32 row-champions (row *k* vs row
  *33-k* in round 5, standard progression after).
- After round 7: 4 teams remain per bracket → mutual **quarterfinal**
  (Draw pairs vs Draw pairs, Process pairs vs Process pairs), then the
  same duplicate-handling rules as RDS Cup for the semifinal/final.
- **Rounds 2-7 are fully generated** via `_pa_round_games()`, same
  real-winner resolution pattern as RDS. **Not yet built**: the mutual
  quarterfinal+ stage (raises `NotImplementedError`).

**Draw seeding**: real, from the PA Cup tab (`pa_cup_seeds.json`).
**Process seeding**: a real, separately-provided seed list
(`pa_process_real_seeds_v2.json`) — not derived from Draw. Two rounds of
data-entry corrections were needed before it was trusted (team name
mismatches — "Resort Area"/"Battle Zone", "Fight Area"/"Cabo Poco" — and
later a full corrected re-send). Always verify a fresh Process list covers
the same 160 teams as Draw before using it.

**Conflict-resolution rules** for Process round 1 (Draw is never modified):
1. Same division: not allowed before round 6.
2. Same region: not allowed before the mutual quarterfinal.
3. Repeat matchup (same two teams already met in Draw or Process, at any
   point): never allowed.
4. **Top-half/bottom-half must never cross** during a swap — with N teams
   remaining, a team can only be swapped with another team in the same
   half (e.g. with 16 remaining, seed #8 can never end up facing #7 or
   better; only #9–16 are valid swap targets).
5. If no valid swap satisfies all of the above, the **original pairing
   stands**, even if it breaks a rule — never leave a game unresolved.

Implemented in `resolve_pa_cup_conflicts()`. Tested against a fully
duplicate Process list (worst case) before trusting it on real data.

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
`CALENDAR_DATA`, and `RANK_ELO_HISTORY` in place. Each reconstruction was
validated by recomputing at round 11 (the last state before this script
existed) and confirming a **byte-identical** match against the
already-published `DATA`/`CALENDAR_DATA` at that round — including the
`dscr_regional_avg`/`dscr_league_avg` fields, confirmed to be the average
of `games.dscr_a`/`dscr_b` (the raw per-game DMAX score, not `d_sqrt_raw`)
across a team's Regional-only/League-only games respectively, and the
`log` field, confirmed to be each team's `[opponent_dex, game_type,
result_a]` history in round order.

**Not yet covered by this script** (stale/absent rather than silently
wrong, until reverse-engineered with the same confidence):
- `STRENGTH_DATA` (RL Strength tab): a 7-component z-scored formula per
  region/division (Rank/OVR/Wins/DSCR/Elo/TOT/GI, weighted, tanh-mapped —
  see the RLStr formula below) computed at the **region-aggregate**
  level, not the single per-team `rlstr_own` value `team_round_ratings`
  already stores. The weighting/summing logic was confirmed by hand
  against real numbers (Indigo's `weighted: -10.43` reproduces exactly
  from its 7 z-components), but the underlying region-level aggregates
  themselves (average rank, wins, DSCR, TOT, and especially GI — Game
  Interest, likely averaged from `games.interest_score` but unconfirmed)
  weren't re-derived with confidence in the time available.
- PA Cup real results: `PA_CUP_DATA` only ever held structural seeding
  (`draw_round1`/`process_round1`/`swap_log`) — there's no
  `PA_REAL_RESULTS` counterpart to RDS Cup's `CUP_REAL_RESULTS`, so the
  PA Cup tab doesn't show real results at all yet, round 1 included. This
  is a missing capability, not a staleness bug — building it would mean
  adding a PA equivalent of the `rdsRound1RowHtml`/`winnerNameHtml`
  seed-aware win/loss rendering already built for RDS Cup.

Everything else (`SCHEDULE_DATA`, `CUP_BRACKET_DATA`/`CUP_REAL_RESULTS`/
`RDS_ROUND2`/`RDS_ROUND3`, `PA_CUP_DATA`'s structural seeding, `RT_DATA`)
is untouched by `regenerate_dashboard.py` because it's genuinely
unaffected by Regional/League/PA results specifically — not a gap, just
out of that script's scope until something that actually changes those
(an RDS round, a Regional Tournament matchday) comes in.

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

## Known open items

- Dashboard regeneration isn't in the CLI yet — still manual script runs.
- Round numbering: the *historical* R1-R5/L1-L2 mapping is confirmed
  against real Archive data; everything from PA Draw round 1 onward uses
  `abs_round_for_event()`'s sequential assignment, which is only "confirmed
  right" in the sense that it's internally consistent, not independently
  cross-checked against a second source the way the historical portion was.
- RDS Cup mutual semifinal/final and PA Cup mutual quarterfinal/semifinal/
  final: the resolution *logic* (`resolve_mutual_stage`, the lane-4-vs-1
  semifinal rule, etc.) is built and tested in isolation, but not wired
  into `_games_for_event` as playable events yet — both raise
  `NotImplementedError` deliberately rather than guessing.
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
  the engine, surfaced in the dashboard's Next Matchday tab as one "batch"
  panel per DECKFIELD batch — each with its own Batch Settings paste box
  and Matchups paste box. Almost every matchday is one batch; **RDS Cup
  matchdays are always three**, because Ribbon/Dream/Star all play on the
  same weekly-schedule slot but a single DECKFIELD batch can only carry one
  Cup Name for every matchup pasted with it (`_games_for_event` already
  combines all three cups into one list for game generation — this just
  splits them back apart for the DECKFIELD-facing export). Format is AGG
  for any two-legged/Bo3 slot (RDS SF/Final, PA QF/SF/Final, Regional
  Tournament MD2 onward — MD1 is the only single-game RT matchday),
  Single Game otherwise. Round-trip verified: rendered the dashboard's
  actual output in a headless DOM, copied a batch's Batch Settings row,
  pasted it into deckfield.html's importer, and confirmed the real
  `<select>` elements land on the correct `<option>` (not just a
  matching string) for both a plain batch (PA Draw R1) and all three
  RDS batches (Ribbon/Dream/Star Draw R1).
