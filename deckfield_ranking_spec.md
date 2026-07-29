# DECKFIELD ranking system — consolidated spec

Working notes from reverse-engineering the S9 stats workbook (`Rankings` and `Calcs` tabs)
into formulas that can drive a database instead of Excel. Everything below was confirmed
step by step — flag anything that reads wrong.

---

## 1. Per-game data (the packed game string)

Format: `[Result][GameType]![OpponentDEX]+[PF]-[PA]^[RawGoalScore]([Spread])[[GI]]{[DSCR]}[NewElo]`

Example: `3R!36+85-28^5.78(17.77)[55]{122.6}2506`

| Segment | Example | Meaning |
|---|---|---|
| Result | 3 | Points earned: 3 = win, 2 = OT win, 1 = OT loss, 0 = loss |
| GameType | R | R=Regional, L=League, S=Cup ("Swiss" in-sheet), P=Playoffs, F=Finals |
| Opponent DEX | 36 | Opponent team ID |
| PF / PA | 85 / 28 | Points for / against, this game |
| RawGoalScore | 5.78 | Raw points/goal scored (not yet used elsewhere in the formulas covered so far) |
| Spread | 17.77 | Pre-game spread, positive if favored, negative if underdog |
| GI | 55 | Game interest score |
| DSCR | 122.6 | "DMAX Score" — per-game DMAX value |
| New Elo | 2506 | Team's Elo after this game |

Derived, not stored: `is_ot = Result IN (1,2)`, `is_win = Result IN (2,3)`.

**One row per game** (both teams' perspectives combined) needs: round, game_type, team_a,
team_b, pf_a, pa_a, result_a (points earned), raw_goal_score_a/b, spread, interest_score,
dscr_a/b, elo_a_after, elo_b_after, ex_bonus_a/b (see §2).

---

## 2. Season point buckets

**Validated exactly against real Season 9 data (all 160 teams) after two corrections
found during migration — see the walkover note below and the RP/LP cross-bonus.**

- **RP** = sum of points earned in Regional games **+ league_wins** (a cross-track
  bonus — confirmed exact across all 160 teams; not documented in earlier drafts)
- **LP** = sum of points earned in League games **+ regional_wins** (same pattern,
  mirrored)
- **SP** = sum of points earned in Cup games × 2 — this multiplied value is what RW and LW
  reference wherever they use `SP` (confirmed, not the raw pre-multiplier sum). No
  cross-bonus on SP — confirmed exact with the plain sum.
- **P** = sum of points earned in Playoff games × 8
- **F** = sum of points earned in Finals games × 12
- **B** (EX Bonus Score) = sum of manual per-round entries from the DECKFIELD HTML — the
  only manually-entered per-round value in the whole system
- **TOT** = RP + LP + SP + P + F + B

**Walkovers:** a short-form entry (e.g. `3S-`: a result digit + game type + dash, no
opponent or stats) awards points and a win/loss count the same as a real game, but has
no opponent and doesn't count toward games_played for any type (regional_games,
league_games) — confirmed by the sheet's own GAMES formula, which explicitly subtracts
these back out. Affects Cup (SP) most often in practice, but the mechanism isn't
type-specific.

---

## 3. RW (Regional Win rating)

**Validated exactly (160/160 teams, zero difference) against real Season 9 data.**
Two corrections from earlier drafts of this spec: the denominator uses the OTHER
track's **games played**, not wins, and does not include a separate cup_wins term
(cup wins' effect already flows in through SP/1000).

**3+ regional games played:**
```
RW = ( RP / (regional_games×3 + league_games)
       + SP/1000
       + playoff_finals_wins×0.005 ) × 100
```

**Under 3 games** (fixed table):

| Games | Record | RW |
|---|---|---|
| 0 | — | 0 (except season-carryover exception, TBD in §8) |
| 1 | 1–0 | 200/3 ≈ 66.67 |
| 1 | 0–1 | 100/3 ≈ 33.33 |
| 2 | 2–0 | 500/6 ≈ 83.33 |
| 2 | 1–1 | 50 |
| 2 | 0–2 | 100/6 ≈ 16.67 |

## 4. LW (League Win rating)

Note: **SW does not exist** — Cup wins are handled elsewhere in the system.
**Validated exactly (160/160 teams, zero difference) against real Season 9 data.**

| League games played | LW |
|---|---|
| 0 | `RW × 0.75` |
| 1 | `average(RW, 1-game table value above)` |
| 2 | `average(RW, 2-game table value above) + (SP/1000 + pW×0.005)` |
| 3+ | `LW_base × (1 + 0.05×(11 − league_division))` |

Where for 3+ games:
```
LW_base = ( LP / (league_games×3 + regional_games)
            + SP/1000
            + playoff_finals_wins×0.005 ) × 100
```

Two corrections from earlier drafts: the denominator uses **regional games played**,
not regional wins, and the 2-game bonus term is the **unscaled** `SP/1000 + pW×0.005`
— NOT the ×100-scaled `SP/10 + pW×0.5` this spec previously stated. That earlier
"consistency" reasoning turned out to be wrong; real data confirms the unscaled form
exactly.

---

## 5. OVR components

All league-wide min/max values are scans across all 160 teams, recomputed fresh each time
they're pulled — not cached.

**PF**
```
PF_norm = average( (PF−min_PF)/(max_PF−min_PF), SQRT(PF−min_PF)/(max_PF−min_PF) ) × 100
```

**PA**
```
PA_norm = (1 − (PA−min_PA)/(max_PA−min_PA)) × 100
```

**PDG**
```
avg_goals_scored = PF / games_played              (team's own)
PD_per_Gl = ((PF−PA) − min_league(PF−PA)) / avg_goals_scored
PDG = PD_per_Gl × 100 / max_league(PD_per_Gl)
```

**SOS** — *depends on opponents' OVR from the previous round (see §7 for the round-by-round
implication)*
```
raw_SOS = average(OVR of every opponent played)
StrMod = raw_SOS × RLStr                    [RLStr = placeholder, §8]
SOS = (StrMod−min_league(StrMod)) / (max_league(StrMod)−min_league(StrMod)) × 100
```

**SOV**
```
VicAvg = (PF−PA) − average(team's own per-game spread)
SOV = (VicAvg−min_league(VicAvg)) / (max_league(VicAvg)−min_league(VicAvg)) × 100
```

**DSCR** (component; distinct from per-game DSCR in §1)
```
D-Sqrt = MIN(10, SQRT(average(per-game DSCR across the season)))
DSCR_component = average( D-Sqrt/max_league(D-Sqrt),
                           (D-Sqrt−min_league(D-Sqrt))/(max_league(D-Sqrt)−min_league(D-Sqrt)) ) × 100
```

**EYE**
```
avg_GI = average(per-game interest score across the season)
X = (PD − min_league(PD)) × (avg_GI/100)          where PD = PF−PA (raw, season total)
Y = SQRT(X/100) × 100
AD = AVERAGE(X, Y) × RLStr                          [placeholder, §8]
Z = AD / max_league(AD)
EYE = AVERAGE(Z, SQRT(Z))
```

**Elo**
```
Elo_component = (Elo−min_league(Elo)) / (max_league(Elo)−min_league(Elo)) × 100
```

**Cups**
```
Cups = (TOT−min_league(TOT)) / (max_league(TOT)−min_league(TOT)) × 100
```

**EX** — fixed once per team per season (manual entry)
```
normalized_EX = (EX−min_league(EX)) / (max_league(EX)−min_league(EX)) × 100
```

---

## 6. OVR assembly

```
Block A = (PF×.5 + PA×.5) × 3
Block B = (PF×.35 + PA×.35 + PDG×.3) × 2
Block C = (SOS×.4 + SOV×.3 + DSCR×.3) × 3
Block D = (EYE×.35 + Elo×.3 + Cups×.35) × 2
Block E = normalized_EX × (N/12)

OVR = (Block A + Block B + Block C + Block D + Block E) / (9 + N/12)
```

`N/12` starts at 12/12 and tapers toward 0/12 over the season. PF and PA are intentionally
double-counted (once alone in Block A, once alongside PDG in Block B) — confirmed
intentional, not a typo.

**Taper schedule** — one round = one matchday = one game per team. **Weeks 1–3 are a
single matchday each; weeks 4–30 are 3 matchdays each** (84 matchdays total, not a flat
3-per-week the whole season). Rankings issued after the last game of a given week use
that week's N value:

| Matchdays (rounds) | Weeks | N | N/12 |
|---|---|---|---|
| 1–12 | 1–6 | 12 | 1.0 |
| 13–15 | 7 | 11 | 0.917 |
| 16–18 | 8 | 10 | 0.833 |
| 19–21 | 9 | 9 | 0.75 |
| 22–24 | 10 | 8 | 0.667 |
| 25–27 | 11 | 7 | 0.583 |
| 28–30 | 12 | 6 | 0.5 |
| 31–33 | 13 | 5 | 0.417 |
| 34–36 | 14 | 4 | 0.333 |
| 37–39 | 15 | 3 | 0.25 |
| 40–42 | 16 | 2 | 0.167 |
| 43–45 | 17 | 1 | 0.083 |
| 46–84 | 18–30 | 0 | 0 |

**Important architectural note:** OVR is round-by-round state, not a pure function of the
season-to-date game log. SOS needs each opponent's OVR *from before the current round* —
in Excel this is done by copy-pasting a snapshot; in the database version it's just "read
last round's stored OVR," computed in strict round order. This means ratings need to live
in a per-team-per-round table, not be recalculated fresh from scratch every time.

---

## 7. Derived display stats

**Grade** — from `Sqrt_col = SQRT(OVR/100)`:

| Grade | OVR range |
|---|---|
| F | < 25 |
| F+ | 25 – 36 |
| EF | 36 – 40.11 |
| E | 40.11 – 44.44 |
| DE | 44.44 – 49 |
| D | 49 – 53.78 |
| CD | 53.78 – 58.78 |
| C | 58.78 – 64 |
| BC | 64 – 69.44 |
| B | 69.44 – 75.11 |
| AB | 75.11 – 81 |
| A | 81 – 87.11 |
| S | 87.11 – 93.44 |
| SS | ≥ 93.44 |

**Climate**
```
Climate = (BasClm/100) × (OVR+50)
```
BasClm: fixed per team per season (like EX), rarely changes season to season.

**Playoff Score**
```
InnerScore = average(Elo_component/100, DSCR_component×3, SOS_component/2.5)
Playoff_Score = average(OVR/4, OVR/5, InnerScore)
```

---

## 8. Fatigue

Not part of the OVR/ranking chain — this is an input the DECKFIELD HTML match engine
consumes directly before simulating a game, based on where a team played its two most
recent games.

```
raw_distance = region_distances[previous_game_host_region][current_game_host_region]
skipped_matchdays = matchdays between those two games with no game played
Fatigue = raw_distance / (2 ^ skipped_matchdays)
```

"Host region" = the region the game was physically played in (a team's own region when
hosting, the opponent's region when traveling) — so a team's fatigue sequence is built
from the succession of host regions across its own games, not a fixed home/away split.

**Region distance matrix** (symmetric, every value a power of 2 — stores cleanly as a
`region_distances` lookup table, ~55 rows for unordered pairs + diagonal rather than the
full 10×10 grid):

| | Indigo | Delta | LilyValley | Vertress | Kalosite | Lanakila | Dynamax | Phoenix | Silver | Terastal |
|---|---|---|---|---|---|---|---|---|---|---|
| Indigo | 1 | 2 | 2 | 8 | 8 | 4 | 8 | 8 | 2 | 8 |
| Delta | 2 | 1 | 2 | 8 | 8 | 4 | 8 | 8 | 2 | 8 |
| LilyValley | 2 | 2 | 1 | 8 | 8 | 4 | 8 | 8 | 2 | 8 |
| Vertress | 8 | 8 | 8 | 1 | 4 | 8 | 4 | 2 | 8 | 4 |
| Kalosite | 8 | 8 | 8 | 4 | 1 | 16 | 2 | 4 | 8 | 2 |
| Lanakila | 4 | 4 | 4 | 8 | 16 | 1 | 16 | 4 | 4 | 16 |
| Dynamax | 8 | 8 | 8 | 4 | 2 | 16 | 1 | 4 | 8 | 2 |
| Phoenix | 8 | 8 | 8 | 2 | 4 | 4 | 4 | 1 | 8 | 4 |
| Silver | 2 | 2 | 2 | 8 | 8 | 4 | 8 | 8 | 1 | 8 |
| Terastal | 8 | 8 | 8 | 4 | 2 | 16 | 2 | 4 | 8 | 1 |

**Season opener:** a carryover fatigue value (from the S8 block, §10) is added at the start
of the season, since there's no "previous game" yet to compare against.

---

## 9. Fixed metadata (no formula)

- **PRIM** — Primary Typing (stored, used by DECKFIELD HTML)
- **Accolades** — flavor text
- **LDEX** — Excel lookup workaround; not needed in the database
- **LEAG** — league division (1–10), fixed per season; feeds LW's `(11−league_division)` term
- **BasClm** — fixed per team per season, rarely changes
- **EX** — fixed per team per season

---

## 10. Season carryover (S8 → S9)

Pulls a team's rating toward the mean at season start, and phases live in-season numbers
in gradually — so the new season doesn't just continue the previous season's power gap.

**Two separate tracks, not one overridden value:** OVR, Fatigue, DSCR, PF, and PA keep
calculating purely from the current season's game log the whole time — the Rankings tab
(and everything derived from it, like Grade and Climate) always reflects the pure,
unblended numbers. The carryover system is a second, separate pipeline that only feeds
the DECKFIELD HTML match engine — during the first 8 matchdays, the *simulation input*
for these five stats is a blended value, while the *displayed ranking* stays pure
throughout. This means each of the five needs two tracked values during that window: the
pure season stat, and the blended match-input stat.

**Seed values**, computed once from a team's season-8 final stats:
```
OVR_seed     = ((previous_season_OVR − 50) × 0.75) + 50
Fatigue_seed = previous_season_Fatigue / 5
DSCR_seed    = previous_season_DSCR        (held as-is)
PF_seed      = previous_season_PF          (held as-is)
PA_seed      = previous_season_PA          (held as-is)
```

**Blend window** — first 8 matchdays of the new season, applied to exactly these five
stats (OVR, Fatigue, DSCR, PF, PA — no others):
```
blended_stat = (NewSeason_stat × X + Seed_stat × Y) / 8
```

| Point in season | X | Y |
|---|---|---|
| Before matchday 1 | 0 | 8 |
| After matchday 1 | 1 | 7 |
| After matchday 2 | 2 | 6 |
| After matchday 3 | 3 | 5 |
| After matchday 4 | 4 | 4 |
| After matchday 5 | 5 | 3 |
| After matchday 6 | 6 | 2 |
| After matchday 7 | 7 | 1 |
| After matchday 8 | 8 | 0 (block no longer active — live stats take over) |

The blended value is what gets sent to the DECKFIELD HTML during this window, overriding
the raw live in-season stat.

DSCR_seed (from §5) is the pre-normalization D-Sqrt figure — `MIN(10, SQRT(average(per-game
DSCR)))`, on its 0–10 scale — not the min-max-normalized 0–100 DSCR OVR-component.

---

## 11. Open placeholders

Flagged during the walkthrough, intentionally not yet defined:

1. **RLStr** (Regional Strength × League Strength) — feeds SOS's StrMod and EYE's AD. Using a
   temporary default of **1** (i.e. no-op multiplier) until the real system is worked out —
   the person building it expects to revise this design.
