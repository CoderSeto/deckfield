# Season 10 turnover plan

Agreed decision by decision with the user on 2026-10-10. **Every item below is
decided. Nothing here has been built yet** except Phase 1 (the S9 freeze).
Build in the order in section C. Where a number is quoted, it was measured
against the frozen S9 record on that date.

**Ground rule for the whole build: S9 is read only from the frozen record.**
S10 never recomputes S9. The workbook and `results/s9/` are an audit trail
only. The frozen record (see "Season 9 is frozen" in CLAUDE.md) is:

| File | Holds |
|---|---|
| `deckfield_dashboard_s9.html` | the final dashboard, byte for byte |
| `archive/s9_final.json` | every dashboard data constant (DATA, standings inputs, cups, RT, WC, HOF, history) |
| `archive/s9_inputs.json` | what S10 needs that the dashboard never showed: raw 0-10 DSCR, Primary Type, base climate, stored and end-of-season accolades, 400-capped DSCR averages, final regional and division standings order |

The one exception is **LegacyP** (B7). It lives only in the workbook, so it is
extracted once into a committed `legacy_points.json`, the same way
`hall_of_fame.json` was.

---

## Done: Phase 1, the S9 freeze

- `deckfield_dashboard_s9.html`: byte-identical copy of the final dashboard.
  `archive/s9_final.json`: its 26 data constants as plain JSON
  (`freeze_season.py`, commit `6421376`).
- `archive/s9_inputs.json` (`freeze_season_inputs.py`). It is written from the
  faithful rebuild and refuses to write unless every overlapping value agrees
  with the frozen record (12 checks x 160 teams). All 20 standings orders were
  checked identical to the frozen page's own `standingsOrder()` in Chromium,
  and the RT seeds agree.
- `archive/SHA256SUMS` + `_check_frozen_archive()` in `regenerate_dashboard.py`:
  it refuses to run if any frozen file changes, goes missing, appears unlisted,
  or is the regenerate target.
- `results/*.csv` moved to `results/s9/`; all 83 still round-trip byte for byte.

---

## A. S9 wrap-up

### A1. S9 accolades (permanent)
Write these into the stored accolade history as `<Title> S9`:
- Cups: Ribbon -> Canalave City, Dream -> Cocona Village, Star -> Casseroya
  Lake, PA -> Cabo Poco.
- RT champions (Region family, display names): Victory Road, National Park,
  Petalburg City, Snowpoint City, Mistralton City, Camphrier Town, Po Town,
  Stow-on-Side, Dolce Island, Casseroya Lake.
- **World Champion S9: Casseroya Lake. World Finalist S9: Nimbasa City.** These
  are new: the export never awarded WC accolades, so `accolades_exported` in
  `s9_inputs.json` lacks them.
- **Division One S9: Canalave City only.** No other division awards.
- **Remove the hardcoded `earned.setdefault("Canalave City", []).append("Division One")`**
  in `export_teams_for_deckfield()`, or it will be stamped onto S10 too.

### A2. Stored history leaves the workbook
- Accolades move into a committed file, built from `s9_inputs.json`'s
  `accolades_exported` (stored history + S9's cup, RT and Division One titles)
  plus A1's two WC titles. S10 then never reads the workbook for them.
- Append S9 to `hall_of_fame.json` (from `HOF_DATA` in `s9_final.json`). S10
  then becomes the season `hall_of_fame()` derives live.

### A3. Season 9 history tab (in the S10 dashboard)
All ten views, as **plain tables of numbers**, reading **only**
`archive/s9_final.json`: Rankings, Standings, RL Strength, Schedule results,
RDS Cup, PA Cup, Regional Playoffs, Qualification, World Championship and
Rank/Elo History. Its data constant is STATIC in the manifest. It must not
reuse the live renderers, so later dashboard changes cannot break it. The
frozen page computes `DATA.teams[].plusminus` (OVR minus the mean OVR) on load
rather than storing it.

### A4. Tag
Create the git tag `s9-final` on commit `6421376`.

---

## B. S10 setup decisions

### B1. Teams
No renames, region moves, additions or removals. Same 160 teams.

### B2. Divisions
- Apply `PROMO_RELEGATION` to the **S9 final division standings**
  (`s9_inputs.json` `division_standings`). Each `P` is up one division, each
  `R` down one (`RRR` = down three). Verified: every new division has exactly
  16 teams (38 stay, 52 move 1, 36 move 2, 26 move 3, 8 move 4).
- **Seed 1-16 inside each new division by S9 division, then S9 finishing
  position.** New Division 1 is S9 Div 1 #1-9, Div 2 #1-4, Div 3 #1-2 and
  Div 4 #1.
- Write the result as S10's `league_pods.json`, using the existing pod layout
  (UL 1-8-9-16, UR 2-7-10-15, LL 4-5-12-13, LR 3-6-11-14).

### B3. Region pods
`conf_pods.json` stays exactly as it is.

### B4. Carryover
- Seeds as already coded (`compute_carryover_seeds`), read from the frozen
  record (`s9_inputs.json` `final_*`):
  - OVR seed = (S9 OVR - 50) x 0.75 + 50
  - fatigue seed = ceil(S9 final fatigue / 5), which is S10's starting fatigue
  - DSCR seed = S9 final raw 0-10 DSCR; PF and PA seeds = S9 final normalized
    components
  - TOT starts at 20; all records start at 0-0
- **Blend window**, matchdays 1-8: (live S10 x X + seed x Y) / 8, where X =
  matchdays played (0/8 -> 8/0). Pure S10 from matchday 9.
- **The blend applies to OUTPUTS ONLY. The engine always calculates on pure S10
  data.**
  - The raw OVR components (PF, PA, PDG, SOS, SOV, DSCR, EYE, Elo, Cups, EX)
    and raw OVR are never blended or overwritten.
  - **Blended OVR is its own stored column** (e.g. `ovr_blend`) =
    blend(raw S10 OVR, OVR seed). Do NOT build it from blended components;
    that would blend the same thing twice.
  - The dashboard shows a **Blended OVR** column during matchdays 1-8. It
    equals raw OVR from matchday 9 and can hide itself.
  - **The roster export** to `deckfield.html` carries blended OVR, PF, PA and
    DSCR. These exist only in the export; the engine never reads them back.
  - **SOS stays on RAW prior OVR.** Using blended OVR would leak the seed into
    the raw components.
- **Rank follows blended OVR** during matchdays 1-8: in the roster (the game
  banner's `#N`, play order, Auto-Play's top-32 tests), the dashboard Rank and
  Rank History. **Exception: RL Strength's rank z-score stays on RAW rank**,
  because RLStr multiplies into SOS.
- **Elo carries over unchanged** from S9 final (S9 itself opened on S8's Elo;
  Canalave City started S9 at 2506). Today the engine falls back to
  `STARTING_ELO` 1800 for a team with no games in the season, which must
  change.
- `get_match_inputs()` / `blend_stat()` exist but are called by nothing today.

### B4a. Raw vs Blended view (per explicit request)
A **fourth Rankings sub-tab** (beside Rankings / Rank History / Elo History),
e.g. **"Raw vs Blended"**. One sortable row per team shows, for OVR, PF, PA,
DSCR (raw 0-10) and Fatigue, the **raw S10 value, the S9 seed and the blended
value** that the game and the rankings use. Rank follows blended OVR (B4). The
header states the current weight, e.g. "Matchday 3: 2/8 S10, 6/8 seed".

**Shown during the blend window only (matchdays 1-8); hidden from matchday 9
on**, when raw and blended are identical. Its data is derived (in the const
manifest), and it hides based on the data, not a hardcoded round number.

### B5. Base climate
Each team keeps its S9 `basclm` (`s9_inputs.json`; originally typed into the
workbook's Rankings sheet, column 69).

### B6. EX seed (`team_seasons.ex`)
**S10 EX seed = round(A + B + LegacyP), whole numbers.**
- **A** = S9 final Cups on the square-root scale, rescaled 0-160:
  `sqrt((TOT - min TOT) / (max TOT - min TOT)) x 160`, using S9's final TOT
  (min 24, max 1421).
- **B** = S9 final OVR rescaled 0-160: `(OVR - min) / (max - min) x 160`
  (min 7.17, max 114.4).
- **LegacyP** = the team's all-time legacy total (B7), added raw.
- Preview: range 0-346, median 142 (S9's actual: 23-362, median 112).
  Casseroya Lake 346, Canalave City 336, Nimbasa City 299, Snowpoint City 290,
  Camphrier Town 290, Lumiose City 271; Boyleland 0.

### B7. Legacy Points
Source: the workbook's `LegacyP` sheet (teams in B2:B58; C = SUM(E:AN); every
other cell typed in). Extract it once into a committed `legacy_points.json`,
one column per season.

Point values (consistent S1-S8):

| Family | Points |
|---|---|
| World Championship | Champion 16, Finalist 8, other semifinalists 4, quarterfinal losers 2 |
| Cup (WCS S3-S5, Swiss S6-S8, PA from S9) | Champion 8, Runner-up 4 |
| **RDS cups (new, from S9)** | **Winner 6, Runner-up 3** |
| League (MRL/UTL S3-S6, First Division from S7) | 8 / 6 / 4 / 2 (S6's two UTL champions shared 3 + 3) |
| Conference (Regional) Champion: the RT champion from S9 | 4 each |

All seasons count equally; nothing fades over time.

Corrections and mapping:
- **S8 Swiss runner-up is Camphrier Town, not National Park.** HOF and HoFOld
  both say Camphrier Town, so the 4 points move.
- The sheet's `Resort Area` is **Battle Zone**.

S9 column to add:
- WC: Casseroya Lake 16, Nimbasa City 8, Camphrier Town 4, Petalburg City 4,
  Blueberry Terarium / Canalave City / Snowpoint City / Mesagoza 2 each.
- PA: Cabo Poco 8, Snowpoint City 4.
- RDS: Canalave City 6 + Snowpoint City 3 (Ribbon), Cocona Village 6 + Nimbasa
  City 3 (Dream), Casseroya Lake 6 + Mesagoza 3 (Star).
- League: Canalave City 8, Pueltown 6, National Park 4, Vermilion City 2.
- The 10 RT champions, 4 each.

Resulting S9 totals: Casseroya Lake 26, Canalave City 16, Snowpoint City 13,
Nimbasa City 11, Camphrier Town / Petalburg City / Cabo Poco / National Park 8,
Pueltown / Cocona Village 6, Mesagoza 5, the five other RT champions 4,
Blueberry Terarium / Vermilion City 2. 66 teams hold legacy points after S9;
Canalave City leads all-time with 70.

**Legacy Points sub-tab on the Hall of Fame**: team x season (S1-S9, then
S10), a Total, sortable, with hover text naming what earned each cell. **S10's
column is filled only after S10 ends**, not live.

### B8. Cups component of OVR (S10 rule)
`Cups = sqrt((TOT - lowest TOT) / (highest TOT - lowest TOT)) x 100`, every
round, replacing the linear form. Its weight is unchanged: 0.35 inside Block D
x 2, i.e. 7% of OVR in weeks 1-6 and 7.8% from week 20. **WC games stay Finals
x12, group stage included**; the square root is the whole fix for the TOT
outlier (Casseroya Lake's S9 TOT of 1421).

### B9. Cup seeding
- **PA Draw**: S9 final Elo, highest = seed 1. **Ties by S9 final OVR** (29
  teams share an Elo with someone).
- **PA Process**: the **new S10 division seeds, laid end to end**. New Div 1's
  1-16 = seeds 1-16, new Div 2's = 17-32, ... Div 10 = 145-160.
- **RDS**, each cup over its own regions (Ribbon = Indigo/Silver/Delta/Lily
  Valley, Dream = Vertress/Phoenix/Lanakila, Star = Kalosite/Dynamax/Terastal).
  Order by S9 final **Regional standings** (`s9_inputs.json`
  `regional_standings`): finishing position (every region's #1, then every
  #2, ...), then **Regional W-L**, then **Regional DSCR capped at 400 per game**
  (B9a, `dscr_regional_avg_cap400`), then S9 final OVR. Dream and Star seeds
  49-64 are byes in both brackets, as in S9. Preview: Ribbon 1 Cianwood City,
  2 Canalave City, 3 Victory Road, 4 Sootopolis City; Dream 1 Po Town; Star 1
  Casseroya Lake. With the cap, just 2 seeds differ from the uncapped preview
  (Ribbon: Mossdeep City #22, Ruins of Alph #21).
- PA conflict resolution (the 5 steps, the region checks through round 6, the
  half-boundary rule) runs unchanged on these seeds.

### B9a. DSCR tiebreaker: each game capped at 400 (S10 onward)
**Problem found 2026-10-10:** the game's per-game DSCR multiplies by the score
ratio (`winnerScore / loserScore`, or the full `winnerScore` when the loser
scores 0; `computeDMAX` in `deckfield.html`). So a shutout or near-shutout
scores 20-70x a normal game: Lavender Town's 42-0 = 5,265.6 against a typical
37.4. Averaged over 15 games, one freak game decided the uncapped tiebreaker
(Lavender Town 196 on the Standings tab; 59 without that game). OVR was never
affected: its DSCR is `min(10, sqrt(average))`.

**Rule (per explicit instruction): each game's DSCR is capped at 400 (= 20^2)
before averaging, for the TIEBREAKER only.** On the displayed `sqrt(avg) x 10`
scale, no single game can then count for more than 200. It applies to:
- the S10 Regional and League standings tiebreak (W-L, H2H, then DSCR). That
  also feeds RT seeding, promotion/relegation and the WC division bids;
- the Regional DSCR step of S10's RDS seeding (B9), from
  `s9_inputs.json`'s `dscr_regional_avg_cap400`.

It does NOT apply to OVR's DSCR component, the roster's DSCR, or S9's frozen
standings.

Measured on S9 (Regional): 12 of 2,400 team-games exceed 400. The top
tiebreaker goes 196 -> 122 (Mistralton City, two capped blowouts), the median
stays 74, and teams above 120 drop from 5 to 1. Lavender Town 196 -> 77,
Mossdeep City 163 -> 82, Floaroma City 135 -> 74, Casseroya Lake 159 -> 107.
League: 8 team-games over 400; the top goes 238 -> 105. A cap of 200 and a
trimmed mean (drop each team's highest and lowest game) were compared and not
chosen.

Build note: the cap lives in BOTH ports of the standings sort, which must stay
in step: `_standings_order` in the engine (it averages `dscr_a`/`dscr_b`) and
`tbOf` in the dashboard. The dashboard needs a capped average in `DATA`;
`dscr_regional_avg` / `dscr_league_avg` are uncapped.

### B10. Calendar
33 weeks, 98 rounds numbered 1-98 in order. **No `_CONFIRMED_ABS_ROUND`
exceptions and no week-6 special case for S10.** Original weeks 5 and 6 are
restored:

| Week | Tue | Thu | Weekend |
|---|---|---|---|
| 1-2 | - | - | R1, R2 |
| 3 | RDS Draw 1 | RDS Process 1 | R3 |
| 4 | RDS Draw 2 | RDS Process 2 | L1 |
| 5 | PA Draw 1 | PA Process 1 | R4 |
| 6 | L2 | L3 | R5 |
| 7-33 | identical to S9 | | |

The RT ends at round 77, which is where the WC field freezes (S9: 74). Key the
EX taper to S10's real calendar weeks (`week_for_round` is S9-shaped).

### B10a. Regional/League schedule outline: every host reversed for S10
"As it's a new season, we need to reverse location of all the games in the
schedule (i.e., A12L becomes A12R)." The round definitions keep their
pairings; only the host flips. There are therefore **two outlines**, which
**alternate every season (confirmed): Outline A in odd seasons (S9), Outline B
in even seasons (S10)**, so S11 returns to A.

Notation: pod round = `away@home` by pod position, played in every pod. Cross
round = relation (A = Across, S = Same, D = Diag), position pattern (the
listed pairs plus their mirrors; `=` means 1v1, 2v2, 3v3, 4v4), and the host
side (L/R for Across and Diag, U/D for Same).

| Def | Outline A (S9) | Outline B (S10) |
|---|---|---|
| 1 | Pod 4@1, 3@2 | Pod 1@4, 2@3 |
| 2 | A 13/24 L | A 13/24 **R** |
| 3 | A 14/23 R | A 14/23 **L** |
| 4 | D 13/24 L | D 13/24 **R** |
| 5 | D 14/23 R | D 14/23 **L** |
| 6 | S 13/24 D | S 13/24 **U** |
| 7 | S 14/23 U | S 14/23 **D** |
| 8 | Pod 1@3, 2@4 | Pod 3@1, 4@2 |
| 9 | S = D | S = **U** |
| 10 | S 12/34 U | S 12/34 **D** |
| 11 | D = L | D = **R** |
| 12 | D 12/34 R | D 12/34 **L** |
| 13 | A = L | A = **R** |
| 14 | A 12/34 R | A 12/34 **L** |
| 15 | Pod 2@1, 4@3 | Pod 1@2, 3@4 |

In S9 these definitions were played in this order, as rounds 1-15. Outline A
matches the user's S9 list exactly and predicts the host of every real S9
game (1,200/1,200 Regional, 1,200/1,200 League). From S10 each group plays the
definitions in its own drawn order (B10b).

- **Outline B is Outline A with every host flipped** (L<->R, U<->D, and each
  pod game's away/home swapped). Implement it as that flip, keyed on season
  parity, from the one `POD_ROUND_DEFS` / `CROSS_ROUND_DEFS` table, not as a
  second hand-copied table that could drift.
- Verified against the real generator: every definition keeps its pairings
  with every host reversed; 120 games, 120 distinct pairs; each team's home
  count flips between 7 and 8.
- **Regions** keep their S9 pods (B3), so every S10 regional game is the S9
  fixture **at the other team's ground**: a true home-and-away across the two
  seasons. **Divisions** are reshuffled by promotion/relegation (B2), so for
  League games the reversal is at the outline level only.
- Fatigue follows automatically (hosting sets `host_region`).

### B10b. Each region and division gets its own round order, redrawn every season
The 15 definitions of the season's outline are put in a **different order for
every region and every division**, drawn fresh each season. Every definition
is self-contained and groups never play each other, so any order is a valid
round robin; only WHEN each pairing happens changes, never who hosts it.

**Rules** (per explicit instruction):
1. **Regional R15 is always Rivalry Week**, the pod 1v2 / 3v4 definition
   (`1 at 2, 3 at 4` in Outline B). Every regional season ends with each pod's
   1 and 2, and 3 and 4, meeting.
2. **No more than 2 home or 2 away games in a row**, counted separately for
   Regional games and for League games (each sequence on its own, never
   interleaved). This matches S9, which never exceeded 2.
3. **Regional:** the previous season's Regional Tournament **finalists** may
   not meet before game 8, and each previous RT **semifinal** pairing may not
   meet before game 4 (from `s9_final.json` `RT_DATA`: MD8 = final, MD6 =
   semifinals).
4. **League:** the previous season's **final Division 1 top three** may not
   meet each other before game 9. They are never relegated, so all three are
   always in Division 1. For S9: Canalave City, Pueltown, National Park.
5. **League, same-region pairs:** two teams from the same region who also meet
   in League play may not have that League game **within one week (7 days,
   inclusive) before or after their Regional game**, on the real calendar.
   Example: a Regional game in R11 (week 16 Weekend) rules out L11 (week 15
   Weekend), L12 and L13 (week 17 Tue/Thu). R1 (week 1) and R15 (week 22)
   block nothing. 110 of S10's 1,200 League games are same-region pairs (8-16
   per division; Division 8 has the most).
6. **League L1 is always the pod 1v2 / 3v4 definition**, the same definition as
   Regional Rivalry Week. League pod pairings change every season with
   promotion/relegation, so unlike the Regional ones it does not keep
   repeating. **Exception:** if two of the Division 1 top three (rule 4) meet
   in it, it cannot be L1. Then it falls at game 9 or later, and L1 is drawn
   like any other round. In S10 the top three are all position 1 in different
   pods, so they never meet in a pod round.
7. **Rule 6 also yields to rule 5, on the League side only** (per explicit
   instruction: it changes the League schedule, never the Regional one). L1 is
   week 4, exactly 7 days from R3 (week 3) and R4 (week 5). If a same-region
   pair meets in its division's 1v2/3v4 definition AND their Regional game was
   drawn in R3 or R4, that division **loses the L1 pin for the season**. Its
   pod round is then placed under rule 5, and L1 is drawn like any other round.
   The Regional draw is never adjusted for this. S10's 7 pairs that could
   trigger it are Virbank City v Anville Town (Div 1), Tagtree Thicket v
   Cascarrafa (5), Eterna City v Pastoria City and Sevii Islands v Fuchsia City
   (6), Mahogany Town v New Bark Town and Aquacorde Town v Snowbelle City (8),
   and Solaceon Town v Jubilife City (10). Each has roughly a 1-in-7 chance, so
   expect 1-2 unpinned divisions in most seasons. A Regional-side safeguard
   was considered and rejected.

**Draw order: Regional first, then League** (per explicit instruction). The
Regional draw has the tighter rules, and rules 5 and 7 depend on when each
Regional game falls.

**Draw uniformly from the valid set**, using the counting walk. It tracks the
last two rounds placed and each definition's allowed game numbers, so every
valid order is equally likely; it is not trial and error. The sampler was
checked against the exact counts (opening-round frequency 24.9% drawn vs 25.4%
exact).

**Exact counts** (S10 inputs, counted exhaustively):

| Group | Orders under rule 2 | ...plus every other rule (trial draw) |
|---|---|---|
| each region (R15 pinned) | 144,543,744 | 36.4M (Kalosite, Dynamax) to 94.2M (Terastal) |
| each division (all free) | 568,737,792 | 3.1M (Div 1) to 86.4M |

The rule-2 count depends only on the pod structure and the outline's hosts,
so it is identical for every region and for every division.

**Trial draw** (seed 10, a throwaway script; NOT the official draw). All seven
rules held: none of the 7 rule-7 pairs drew R3/R4, so all 10 divisions kept
the L1 pin. **An independent checker found 0 failures.** It shares no code
with the draw: hosts were compared with S9's real games reversed, Division 1's
top three were taken from the Hall of Fame, and the calendar was typed
separately. It also caught all 8 deliberately broken copies, each with its own
message. A side note on rule 2: the 1v2/3v4 definition is hard to place
mid-season without a third home or away game in a row, which is partly why
rule 6 puts it at L1.

**Build notes:**
- The draw is **seeded by season number**, so it is reproducible. It is
  written to a committed per-season file (e.g. `schedule_orders_s10.json`:
  group -> list of 15 definitions, plus each division's pin status and
  reason) and never changes mid-season. With the same method and seed, the
  official draw should reproduce the trial.
- `R n` / `L n` means "the nth definition in this group's order". Every lookup
  that uses pod rounds must go through it: `_games_for_event`,
  `SCHEDULE_DATA`, the Schedule tab and the test run.
- The independent checker is committed alongside the draw and run on the
  written file every season; never trust the generator alone.

### B10c. Dashboard changes for the per-group orders (per explicit request)
- **Standings gets a third sub-tab** showing each group's order for the season,
  **regions and divisions** (confirmed): game 1-15 -> definition (e.g.
  `Diag 1v3/2v4 at R`). It marks Rivalry Week and the L1 pod round, or which
  exception unpinned it.
- **The Schedule tab's round picker becomes 30 buttons**: `League 1 ... 15`
  and `Regional 1 ... 15`, replacing the dropdown with its Pod / Across /
  Diag labels (they no longer mean anything once every group has its own
  order). It still opens on the most recently completed round
  (`latestCompletedRound`).

### B11. Rules carried over unchanged
- Tournament bonuses:
  - RDS 15 / 30
  - PA 30 / 45 / 60
  - RT 15 / 30 / 45
  - WC 40 / 60 / 80 / 100 / 120
- WC qualification:
  - division bids 5/4/3/3/2/1
  - PA 4, RDS 2 per cup
  - RT 20 (3/2/1 by allocation)
  - then at-large
- Region allocation = S10/2 + S9/3 + S8/6. S9's term comes from the frozen
  final regional strength (`STRENGTH_DATA`), z-scored and pushed through the
  same transform as the S8/S7 priors. Re-key `REGION_PRIOR_STRENGTH` as "1
  season ago / 2 seasons ago" so S11 needs no edit.
- RT Factor Modifier divisors 4/4/8/12/16.
- WC:
  - snake draw with a cap of 2 teams per region per group
  - group starting points 4/3/2/1
  - Play-in formats
  - playoff seeding score
  - Adv on group / Play-in / bracket
  - all games Finals x12
- EX taper: full weight through week 6, zero from week 20.

### B12. Results files
`results/s10/s10-w<week>-<day>-<event>.csv`, e.g.
`results/s10/s10-w1-weekend-r-1.csv`. The Matchday Pack's `file=` must follow.

### B13. Going forward
- **S10 titles become accolades automatically** at the end of S10, by the same
  rules as S9: the four cup winners, RT champions, WC Champion + Finalist, and
  the Division One champion, as `<Title> S10`, derived from results.
- S10's Legacy column is filled when S10 ends (B7).
- At the end of S10: `python3 freeze_season.py 10`, then
  `python3 freeze_season_inputs.py 10`.

---

## C. Build order

1. **Season as a setting** everywhere: `SEASON = 9` in
   `regenerate_dashboard.py` and `migrate_s9.py`, the CLI `--season` default,
   and the dashboard title/subtitle. Per-season `WEEKLY_SCHEDULE` (B10), the
   season-parity outline (B10a) and `results/s10/` naming (B12).
2. **S10's starting point, from the frozen record** (`s9_final.json`,
   `s9_inputs.json`): `team_seasons` rows (division B2, basclm B5, EX seed B6,
   starting fatigue B4), carryover seeds, Elo carry, S10 `league_pods.json`,
   and the cup seed files (B9).
3. **The schedule draw** (B10b): the seeded draw, Regional then League,
   written to `schedule_orders_s10.json`, plus the committed independent
   checker.
4. **Engine rules**: the square-root Cups (B8), `ovr_blend` + blended rank
   with RLStr and SOS kept raw (B4), Elo carry, the 400 DSCR tiebreak cap in
   `_standings_order` (B9a), and removing the Division One hardcode (A1).
5. **History**: the accolades file (A1/A2), `hall_of_fame.json` S9,
   `legacy_points.json` (B7).
6. **Dashboard**: the Season 9 history tab (A3), the Legacy Points sub-tab
   (B7), the Raw vs Blended sub-tab (B4a), the Standings orders sub-tab and
   the 30 Schedule buttons (B10c), and the capped DSCR in `DATA` / `tbOf`
   (B9a). New constants are classified in the manifest.
7. **Rewrite the CLAUDE.md runbook for S10**: the rebuild comes from the
   frozen record + `results/s10/`, not migrate + `results/`.
8. **Test run on a scratch database**:
   - `next-matchday` = week 1 Weekend R1, round 1
   - Regional hosts are S9's real games reversed (B10a)
   - the schedule checker passes on `schedule_orders_s10.json` (all B10b
     rules, all orders distinct, pin status and reasons correct)
   - the roster at MD1 = 100% seed; the blend tapers correctly through MD9;
     Raw vs Blended shows during MD1-8 and hides from MD9
   - Elo carried, pods generate, the 400 cap applied in both standings ports
   - RDS and PA brackets generate from the new seeds with clean conflict
     resolution
   - the Matchday Pack loads in `deckfield.html`
   - every tab renders with zero `pageerror`
9. **Tag `s9-final`** (A4). Go live with S10 Round 1.
