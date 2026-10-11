# S10 schedule draw: prototypes (reference, not production)

Throwaway scripts written while settling SEASON10_PLAN.md section B10b, kept
because they are the **reference for build step 3** (the schedule draw).
The official draw must reproduce `schedule_orders_s10_trial.json` from the
same method and seed. Run any of these from anywhere; paths are
repo-relative.

| File | What it is |
|---|---|
| `draw.py` | Shared machinery: Outline B hosts (S9's tables flipped), the S10 calendar, the one-week window, and `solver()`. The solver counts valid orders exactly and samples them uniformly by tracking the last two rounds placed. Its own main block is an earlier trial (rules 1-5 only). |
| `draw_s10.py` | **The trial draw with all seven rules** (seed 10): Regional first, then League. Writes `schedule_orders_s10_trial.json`. About 80 s. |
| `check_s10.py` | **Independent checker.** Shares no code with the draw: hosts are compared with S9's real games reversed (frozen `SCHEDULE_DATA`), the Division 1 top three come from the Hall of Fame, and the calendar is typed separately. `python3 check_s10.py schedule_orders_s10_trial.json` -> `FAILURES: 0`. |
| `count2.py` | Exhaustive count of valid orders under the cap of 2 plus the RT and Division 1 protections (B10b's count table). About 4 min. |
| `schedule_orders_s10_trial.json` | The trial draw: every region's and division's order and game list, plus pin status and reasons. |
| `S10_schedule_trial.md` | Readable report of the trial, with RT final and semifinal rematches and Division 1 protected games highlighted. |

Verified 2026-10-10:
- Rerunning `draw_s10.py` from this folder (with a different working
  directory) reproduced the trial **byte for byte**.
- The checker passes, and it caught all 8 deliberately broken copies
  (Rivalry Week moved, a host flipped back, L1 moved, a falsified pin flag,
  RT finalists moved to game 1, Canalave City v Pueltown moved to game 2, two
  divisions sharing an order, a same-region game next to its Regional game).

The draw prototypes swap Outline B into `deckfield_ratings` **in-process
only** (by reassigning `POD_ROUND_DEFS` / `CROSS_ROUND_DEFS`).

**Promoted 2026-10-10.** The production draw is `draw_schedule.py` and the
checker `check_schedule.py` (repo root); `seasons/s10/schedule_orders.json`
equals `schedule_orders_s10_trial.json` byte for byte. The engine now flips the
outline itself by season parity (`outline_defs`), and these prototypes flip it
again in-process -- so run them as Season 9 against an S9 database
(`DECKFIELD_SEASON=9 DECKFIELD_DB=<s9 db>`), which is what they were written
against (they read `division_standings_seeds(9, ...)`).
