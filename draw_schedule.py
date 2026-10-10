#!/usr/bin/env python3
"""
Draw a season's Regional and League round orders (SEASON10_PLAN B10b).

    python3 draw_schedule.py 10

Every region and every division plays the fifteen round definitions of the
season's outline (Outline A in odd seasons, B in even ones -- outline_defs)
in its OWN order, drawn fresh each season. Only WHEN each pairing happens
changes, never who hosts it. The rules, per explicit instruction:

1. Regional R15 is always Rivalry Week: the pod 1v2 / 3v4 definition.
2. No more than 2 home or 2 away games in a row, counted separately for
   Regional games and for League games.
3. Regional: last season's Regional Tournament finalists may not meet before
   game 8, and each of its semifinal pairings not before game 4.
4. League: last season's final Division 1 top three may not meet each other
   before game 9 (they are never relegated, so all three are in Division 1).
5. League, same-region pairs: no League game within one week (7 days,
   inclusive) before or after the pair's Regional game, on the real calendar.
6. League L1 is the pod 1v2 / 3v4 definition, unless two of the Division 1
   top three meet in it (then it falls at game 9 or later).
7. Rule 6 also yields to rule 5, on the League side only: a division loses
   the L1 pin for the season when a same-region pair meets in its pod
   1v2 / 3v4 definition and their Regional game falls within 7 days of L1.

Regional is drawn first, then League (rules 5 and 7 depend on when each
Regional game falls). Each group's order is drawn UNIFORMLY from every order
that satisfies the rules, by an exact counting walk over (the set of
definitions placed, the last two placed) -- not trial and error. The draw is
seeded by the season number, so it is reproducible, and written once to
seasons/s<N>/schedule_orders.json, which never changes mid-season.

Inputs: the previous season's FROZEN record (its RT_DATA for rule 3, its
final Division 1 order for rule 4), conf_pods.json and the season's own
league_pods.json (prepare_season.py). Promoted from prototypes/s10_schedule/
(draw.py + draw_s10.py) and required to reproduce that trial draw exactly.

Check the written file with the independent checker, check_schedule.py,
which shares no code with this one. Never trust the generator alone.
"""
import itertools
import json
import os
import random
import sys
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
DAY_OFFSET = {"Tue": 1, "Thu": 3, "Weekend": 5}


def solver(sched, forbid, pin15):
    """(count, sample(rng)) over the orders of one group's definitions.

    sched: {definition: [(home, away), ...]}. forbid: {definition: set of game
    numbers it may not be played at}. pin15: definition 15 is fixed as the
    last game (Regional Rivalry Week). The walk tracks the set of definitions
    placed so far and the last two, which is all rule 2 needs to know, so
    g() counts the valid completions of any partial order exactly and
    sample() draws each next definition in proportion to its completions --
    every valid order is equally likely."""
    teams = sorted({t for g in sched.values() for p in g for t in p})
    hv = {r: tuple(t in {h for h, _ in sched[r]} for t in teams) for r in sched}
    bad = lambda a, b, c: any(x == y == z for x, y, z in zip(hv[a], hv[b], hv[c]))
    R = tuple(range(1, 15)) if pin15 else tuple(range(1, 16))
    full = (1 << len(R)) - 1

    @lru_cache(maxsize=None)
    def g(mask, a, b):
        k = bin(mask).count("1") + 1
        if mask == full:
            return 1 if not pin15 or not bad(a, b, 15) else 0
        tot = 0
        for i, r in enumerate(R):
            if mask >> i & 1 or k in forbid.get(r, ()):
                continue
            if a is not None and bad(a, b, r):
                continue
            tot += g(mask | 1 << i, b, r)
        return tot

    total = g(0, None, None)

    def sample(rng):
        mask, a, b, out = 0, None, None, []
        while mask != full:
            k = len(out) + 1
            opts = [(g(mask | 1 << i, b, r), i, r) for i, r in enumerate(R)
                    if not mask >> i & 1 and k not in forbid.get(r, ())
                    and (a is None or not bad(a, b, r))]
            x = rng.randrange(sum(c for c, _, _ in opts))
            for c, i, r in opts:
                if x < c:
                    break
                x -= c
            mask, a, b = mask | 1 << i, b, r
            out.append(r)
        return out + ([15] if pin15 else [])

    return total, sample


def calendar_days(db):
    """{"R": {n: day}, "L": {n: day}} -- the calendar day (week x 7 + Tue 1 /
    Thu 3 / Weekend 5) each Regional and League round n is played on, from
    the engine's own calendar for the season."""
    day = {"R": {}, "L": {}}
    for wk in db.WEEKLY_SCHEDULE:
        for d in ("Tue", "Thu", "Weekend"):
            slot = wk[d]
            if slot is not None and slot[0] in ("R", "L"):
                day[slot[0]][slot[1]] = int(str(wk["week"]).split()[0]) * 7 + DAY_OFFSET[d]
    assert sorted(day["R"]) == list(range(1, 16)) and sorted(day["L"]) == list(range(1, 16))
    return day


def meet(sched, x, y):
    return next(r for r, games in sched.items() for h, a in games if {h, a} == {x, y})


def draw(season):
    import deckfield_ratings as db
    if db.CURRENT_SEASON != season:
        raise SystemExit(f"run as season {season}: DECKFIELD_SEASON={season} python3 draw_schedule.py {season}")
    prev = season - 1
    final = db.frozen_season_data(prev)
    inputs = db.frozen_season_inputs(prev)
    region_of = {t["name"]: t["region"] for t in final["DATA"]["teams"]}
    day = calendar_days(db)

    def blocked_l(r_game):
        """League game numbers within 7 days of Regional game r_game."""
        return {n for n, d in day["L"].items() if abs(d - day["R"][r_game]) <= 7}

    out = {"season": season, "seed": season, "outline": db.outline_for_season(season),
           "round_defs": {r: db.round_def_label(r, season) for r in range(1, 16)},
           "regions": {}, "divisions": {}}
    rng = random.Random(season)

    reg_game = {}
    for region, pods in db.load_conf_pods().items():
        s = db.generate_pod_schedule({k: v["teams"] for k, v in pods.items()}, season)
        rt = final["RT_DATA"][region]
        forbid = {}
        protected = [((rt["8"][0]["home"], rt["8"][0]["away"]), 8)]
        protected += [((m["home"], m["away"]), 4) for m in rt["6"]]
        for (x, y), first in protected:
            r = meet(s, x, y)
            if r != 15:
                forbid.setdefault(r, set()).update(range(1, first))
        n, sample = solver(s, forbid, True)
        order = sample(rng)
        pos = {r: i + 1 for i, r in enumerate(order)}
        for r, games in s.items():
            for h, a in games:
                reg_game[frozenset((h, a))] = pos[r]
        out["regions"][region] = {"valid_orders": n, "order": order,
                                  "games": {str(i + 1): s[r] for i, r in enumerate(order)}}

    top3 = inputs["division_standings"]["1"][:3]
    for d, block in db.load_season_json("league_pods.json", season).items():
        pods = {k: v["teams"] for k, v in block.items()}
        names = [t for ts in pods.values() for t in ts]
        s = db.generate_pod_schedule(pods, season)
        reasons = []
        if d == "1":
            reasons += [f"D1 top three {a} v {b} meet in it"
                        for a, b in itertools.combinations(top3, 2) if meet(s, a, b) == 15]
        reasons += [f"{x} v {y} (same region) play Regional game {reg_game[frozenset((x, y))]}, within 7 days of L1"
                    for x, y in s[15] if region_of[x] == region_of[y]
                    and 1 in blocked_l(reg_game[frozenset((x, y))])]
        forbid = {}
        for x, y in itertools.combinations(names, 2):
            if region_of[x] == region_of[y]:
                forbid.setdefault(meet(s, x, y), set()).update(blocked_l(reg_game[frozenset((x, y))]))
        if d == "1":
            for a, b in itertools.combinations(top3, 2):
                forbid.setdefault(meet(s, a, b), set()).update(range(1, 9))
        if not reasons:
            forbid[15] = set(forbid.get(15, set())) | set(range(2, 16))
        n, sample = solver(s, forbid, False)
        order = sample(rng)
        out["divisions"][d] = {"valid_orders": n, "order": order, "l1_pinned": not reasons,
                               "unpinned_because": reasons, "pods": pods,
                               "games": {str(i + 1): s[r] for i, r in enumerate(order)}}
    return out


def main():
    args = [a for a in sys.argv[1:] if a != "--force"]
    if len(args) != 1 or not args[0].isdigit():
        sys.exit("usage: python3 draw_schedule.py <season> [--force]")
    season = int(args[0])
    os.environ.setdefault("DECKFIELD_SEASON", str(season))
    out = draw(season)
    path = os.path.join(HERE, "seasons", f"s{season}", "schedule_orders.json")
    text = json.dumps(out, indent=1)
    if os.path.exists(path):
        with open(path) as f:
            if f.read() == text:
                print(f"{os.path.relpath(path, HERE)} already up to date")
                return
        if "--force" not in sys.argv[1:]:
            sys.exit(f"refusing to overwrite {os.path.relpath(path, HERE)}: a season's drawn orders never "
                     f"change once written (pass --force only before the season's first game)")
    with open(path, "w") as f:
        f.write(text)
    pinned = sum(1 for d in out["divisions"].values() if d["l1_pinned"])
    print(f"wrote {os.path.relpath(path, HERE)} (outline {out['outline']}, seed {season}; "
          f"{pinned}/10 divisions keep the L1 pin)")


if __name__ == "__main__":
    main()
