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
  1)`, rounds 1–14) since OVR feeds SOS round-to-round. Real effect, not a
  no-op: Canalave City's round-14 OVR went from ~102 to 113.23 (RW 102.4,
  LW 153.6 vs. the old inputs PF_norm 93.48/PA_norm 94.79 — Block A's raw
  inputs got meaningfully larger). Season-wide round-14 OVR range shifted
  to 9.77–113.23 (avg 56.9), still a sane distribution, no NaN/negative
  blowups.
- **LW runs structurally higher than RW, especially in low-numbered
  divisions — not a bug, an artifact of LW's division multiplier having no
  RW counterpart.** Flagged with Canalave City at round 14 (LW 153.6 vs. RW
  102.4) as the concrete example. Both start from the same saturation
  point for an unbeaten record — RW's raw formula and LW's `LW_base` are
  literally both `102.4` here (RP/RP-denominator = LP/LP-denominator =
  1.0 in each, since undefeated in both tracks):
  ```
  RW      = (RP/(regional_games×3+league_games) + SP/1000 + pW×0.005) × 100
          = (18/(5×3+3) + 24/1000 + 0) × 100 = (18/18 + 0.024) × 100 = 102.4
  LW_base = (LP/(league_games×3+regional_games) + SP/1000 + pW×0.005) × 100
          = (14/(3×3+5) + 24/1000 + 0) × 100 = (14/14 + 0.024) × 100 = 102.4
  LW      = LW_base × (1 + 0.05×(11−league_division))
          = 102.4 × (1 + 0.05×(11−1)) = 102.4 × 1.5 = 153.6
  ```
  RW has no equivalent post-multiplier — LW's `×(1 + 0.05×(11−division))`
  term is applied to LW only, per spec §4, and was already validated exact
  against real S9 data (160/160 teams) before this session, so it's a
  faithful port, not a mistake. For division 1 it's a full ×1.5; it shrinks
  toward ×1.05 for division 10. This asymmetry is exactly why LW numbers
  read as "too high" this early in the season (round 14, only 3 league
  games played) — nothing wrong with the computation itself, just an
  emergent property of the division multiplier at small sample sizes, now
  more visible since Block A routes LW straight into OVR.
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
- After round 7: 4 teams remain per bracket → mutual **quarterfinal**
  (Draw pairs vs Draw pairs, Process pairs vs Process pairs), then the
  same duplicate-handling rules as RDS Cup for the semifinal/final.
- **Rounds 2-7 are fully generated** via `_pa_round_games()`, same
  real-winner resolution pattern as RDS. **Not yet built**: the mutual
  quarterfinal+ stage (raises `NotImplementedError`).
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
  - **Rounds 5-7 (the champions bracket among the 32 row-champions) do
    NOT yet get conflict resolution** — deliberately deferred, not
    silently skipped. Unlike rounds 1-4, there's no fixed "seed pool" of
    not-yet-committed teams to swap at this stage; every entrant is
    already a fully-determined real team by the time round 5 starts, so a
    "swap" there would mean reassigning which two already-decided
    champions face each other (a re-seeding operation, not a seed-holder
    swap) — a genuinely different, untested mechanism with no real data
    to validate against yet (round 4 hasn't been played). Round 5-7
    pairing generation itself (seed-based home/away, no entrant-swap
    conflicts) was re-verified working end to end regardless.
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
built for round 1 only; extended to rounds 2-4 the same day (see above) —
this exact 5-step order applies at every round, just scoped each round to
that round's own tier-seed range for swaps:**
1. (up until round 6) Check the **Draw** for same-region/same-division
   pairings and make appropriate switches.
2. (up until the mutual quarterfinal) Check the **Process** for any
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
so their swap_log is always `[]`) and attach the result as
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
