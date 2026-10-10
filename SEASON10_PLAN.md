# Season 10 turnover plan

Agreed decision by decision with the user on 2026-10-10. **Every item below is
decided; nothing here has been built yet** except Phase 1 (the S9 freeze).
Build in the order at the bottom. Where a number is quoted, it was measured
against the frozen S9 archive on that date.

Ground rule for the whole build: **S9 is read only from the frozen archive**
(`archive/s9_final.json`, `deckfield_dashboard_s9.html`; see "Season 9 is
frozen" in CLAUDE.md). S10 never recomputes S9. The workbook and
`results/s9/` are an audit trail only.

---

## Done: Phase 1, the S9 freeze (commit `6421376`)

- `deckfield_dashboard_s9.html`: byte-identical copy of the final dashboard.
- `archive/s9_final.json`: 26 data constants as plain JSON.
- `archive/SHA256SUMS` + `_check_frozen_archive()` in `regenerate_dashboard.py`.
- `results/*.csv` moved to `results/s9/`.
- `freeze_season.py <N>`: reusable at the end of any season.

---

## A. S9 wrap-up

### A1. S9 accolades (permanent)
Write these into the stored accolade history as `<Title> S9`:
- Cups: Ribbon -> Canalave City, Dream -> Cocona Village, Star -> Casseroya
  Lake, PA -> Cabo Poco.
- RT champions (Region family, display names): Victory Road, National Park,
  Petalburg City, Snowpoint City, Mistralton City, Camphrier Town, Po Town,
  Stow-on-Side, Dolce Island, Casseroya Lake.
- **World Champion S9: Casseroya Lake. World Finalist S9: Nimbasa City** (new;
  the export never awarded WC accolades).
- **Division One S9: Canalave City only.** No other division awards.
- **Remove the hardcoded `earned.setdefault("Canalave City", []).append("Division One")`**
  in `export_teams_for_deckfield()`, or it will be stamped onto S10 too.

### A2. Stored history leaves the workbook
- `teams.accolades` is currently written by `migrate_accolades()` from the
  workbook. Move accolades into a committed file that includes S9, so S10 does
  not depend on the workbook for them.
- Append S9 to `hall_of_fame.json` (from `HOF_DATA` in the archive). S10 then
  becomes the season `hall_of_fame()` derives live.

### A3. Season 9 history tab (in the S10 dashboard)
All ten views, as **plain tables of numbers**, reading **only**
`archive/s9_final.json`: Rankings, Standings, RL Strength, Schedule results,
RDS Cup, PA Cup, Regional Playoffs, Qualification, World Championship,
Rank/Elo History. Its data constant is STATIC in the manifest. It must not
reuse the live renderers, so later dashboard changes cannot break it. Note
that the frozen page computes `DATA.teams[].plusminus` (OVR minus the mean
OVR) on load rather than storing it.

### A4. Tag
Create the git tag `s9-final` on commit `6421376`.

---

## B. S10 setup decisions

### B1. Teams
No renames, region moves, additions or removals. Same 160 teams.

### B2. Divisions
- Apply `PROMO_RELEGATION` to the **S9 final division standings**. Each `P` is
  up one division, each `R` down one (`RRR` = down three). Verified: every new
  division has exactly 16 teams (38 stay, 52 move 1, 36 move 2, 26 move 3, 8
  move 4).
- **Seed 1-16 inside each new division by S9 division, then S9 finishing
  position.** New Division 1 is S9 Div 1 #1-9, Div 2 #1-4, Div 3 #1-2, Div 4
  #1.
- Write the result as S10's `league_pods.json`, using the existing pod layout
  (UL 1-8-9-16, UR 2-7-10-15, LL 4-5-12-13, LR 3-6-11-14).
- Take the S9 standings from the frozen record. The preview used
  `division_standings_seeds(9, div)`; confirm it matches the archive's
  standings before using it.

### B3. Region pods
`conf_pods.json` stays exactly as it is.

### B4. Carryover
- Seeds as already coded (`compute_carryover_seeds`):
  - OVR seed = (S9 OVR - 50) x 0.75 + 50
  - fatigue seed = ceil(S9 final fatigue / 5), which is S10's starting fatigue
  - DSCR seed (raw 0-10), PF and PA seeds (normalized components) = S9 final
  - TOT starts at 20; all records start at 0-0
- **Blend window**, matchdays 1-8: (live S10 x X + seed x Y) / 8, X =
  matchdays played (0/8 -> 8/0); pure S10 from matchday 9.
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
- **Rank follows blended OVR** during matchdays 1-8: in the roster (game banner
  `#N`, play order, Auto-Play's top-32 tests), the dashboard Rank and Rank
  History. **Exception: RL Strength's rank z-score stays on RAW rank**, because
  RLStr multiplies into SOS.
- **Elo carries over unchanged** from S9 final (S9 itself opened on S8's Elo;
  Canalave City started S9 at 2506). Today the engine falls back to
  `STARTING_ELO` 1800 for a team with no games in the season, which must
  change.
- `get_match_inputs()` / `blend_stat()` exist but are called by nothing today.

### B5. Base climate
Each team keeps its S9 `basclm` (2-10 integers, typed into the workbook's
Rankings!BQ, column 69).

### B6. EX seed (`team_seasons.ex`)
**S10 EX seed = round(A + B + LegacyP), whole numbers.**
- **A** = S9 final Cups on the square-root scale, rescaled 0-160:
  `sqrt((TOT - min TOT) / (max TOT - min TOT)) x 160`, using S9's final TOT
  from the archive (min 24, max 1421).
- **B** = S9 final OVR rescaled 0-160: `(OVR - min) / (max - min) x 160`
  (min 7.17, max 114.4).
- **LegacyP** = the team's all-time legacy total (B7), added raw.
- Preview: range 0-346, median 142 (S9's actual: 23-362, median 112).
  Casseroya Lake 346, Canalave City 336, Nimbasa City 299, Snowpoint City 290,
  Camphrier Town 290, Lumiose City 271; Boyleland 0.

### B7. Legacy Points
Source: the workbook's `LegacyP` sheet (B2:B58 teams, C = SUM(E:AN), every
other cell typed in). Move it into a committed file (e.g.
`legacy_points.json`), one column per season.

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
  both say Camphrier Town; move the 4 points.
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

**Legacy Points sub-tab on the Hall of Fame**: team x season (S1-S9, then S10),
a Total, sortable, with hover text naming what earned each cell. **S10's column
is filled only after S10 ends**, not live.

### B8. Cups component of OVR (S10 rule)
`Cups = sqrt((TOT - lowest TOT) / (highest TOT - lowest TOT)) x 100`, every
round. This replaces the linear form. Its weight is unchanged: 0.35 inside
Block D x 2, i.e. 7% of OVR in weeks 1-6, 7.8% from week 20. **WC games stay
Finals x12, group stage included**; the square root is the whole fix for the
TOT outlier (Casseroya Lake's S9 TOT of 1421).

### B9. Cup seeding
- **PA Draw**: S9 final Elo, highest = seed 1. **Ties by S9 final OVR** (29
  teams share an Elo with someone).
- **PA Process**: the **new S10 division seeds, laid end to end**. New Div 1's
  1-16 = seeds 1-16, new Div 2's = 17-32, ... Div 10 = 145-160.
- **RDS** (each cup over its own regions; Ribbon = Indigo/Silver/Delta/Lily
  Valley, Dream = Vertress/Phoenix/Lanakila, Star = Kalosite/Dynamax/Terastal):
  S9 final **Regional standings**, ranked by finishing position (every region's
  #1, then every #2, ...), then **Regional W-L**, then **Regional DSCR**, then
  S9 final OVR. Dream and Star seeds 49-64 are byes in both brackets, as in S9.
  Preview: Ribbon 1 Cianwood City, 2 Canalave City, 3 Victory Road, 4 Sootopolis
  City; Dream 1 Po Town; Star 1 Casseroya Lake. DSCR settled 59 ties; the OVR
  fallback was not needed.
- PA conflict resolution (the 5 steps, the region checks through round 6, the
  half-boundary rule) runs unchanged on these seeds.

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
- Region allocation = S10/2 + S9/3 + S8/6. S9's term comes from the archive's
  final regional strength, z-scored and pushed through the same transform as
  the S8/S7 priors. Re-key `REGION_PRIOR_STRENGTH` as "1 season ago / 2
  seasons ago" so S11 needs no edit.
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
- `python3 freeze_season.py 10` closes S10.

---

## C. Build order

1. Make the season number a setting everywhere: `SEASON = 9` in
   `regenerate_dashboard.py` and `migrate_s9.py`, the CLI `--season` default,
   and the dashboard title/subtitle. Add a per-season `WEEKLY_SCHEDULE` (B10)
   and `results/s10/` naming (B12).
2. Build S10's starting point from the archive: `team_seasons` rows (division
   B2, basclm B5, EX seed B6, starting fatigue B4), carryover seeds, Elo carry,
   S10 `league_pods.json`, and the cup seed files (B9).
3. Engine rules: the square-root Cups (B8), `ovr_blend` + blended rank with
   RLStr and SOS kept raw (B4), Elo carry, removing the Division One hardcode.
4. History: S9 accolades file, `hall_of_fame.json` S9, `legacy_points.json`
   (B7), the Season 9 history tab (A3), and the Legacy Points sub-tab.
5. Rewrite the CLAUDE.md runbook for S10: the rebuild comes from the archive +
   `results/s10/`, not migrate + `results/`.
6. **Test run on a scratch database**:
   - `next-matchday` = week 1 Weekend R1, round 1
   - the roster at MD1 = 100% seed
   - Elo carried, pods generate
   - RDS and PA brackets generate from the new seeds with clean conflict
     resolution
   - the Matchday Pack loads in `deckfield.html`
   - the blend tapers correctly through MD9
   - every tab renders with zero `pageerror`
7. Tag `s9-final` (A4). Go live with S10 Round 1.
