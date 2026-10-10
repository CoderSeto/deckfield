#!/usr/bin/env python3
"""
Independent checker for a season's drawn Regional/League orders
(SEASON10_PLAN B10b).

    python3 check_schedule.py 10            # checks seasons/s10/schedule_orders.json
    python3 check_schedule.py 10 other.json

Run it on the written file every season; never trust the generator alone.
It shares NO code with draw_schedule.py or the engine, on purpose:

- hosts are compared with the previous season's REAL games reversed (its
  frozen SCHEDULE_DATA) -- the outlines alternate every season and the
  regional pods never change, so every regional game must be last season's
  fixture at the other team's ground;
- the Division 1 top three come from the previous season's Hall of Fame
  plaque (frozen HOF_DATA), not from the standings code;
- the calendar is typed here, separately from the engine's WEEKLY_SCHEDULE;
- the divisions are checked against the season's own league_pods.json.

Promoted from prototypes/s10_schedule/check_s10.py. Prints FAILURES: 0 when
every rule holds and exits non-zero otherwise.
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The Season 10+ calendar of Regional (R) and League (L) rounds, typed from
# SEASON10_PLAN B10: [Tue, Thu, Weekend] for each week that holds one.
WEEKS = {1: "--R", 2: "--R", 3: "--R", 4: "--L", 5: "--R", 6: "LLR", 7: "--L", 8: "--R", 9: "LLR",
         10: "--L", 11: "--R", 12: "LLR", 13: "--L", 14: "--R", 15: "--L", 16: "--R", 17: "LLR",
         18: "--L", 19: "--R", 20: "--L", 21: "--R", 22: "--R"}


def main():
    if len(sys.argv) not in (2, 3) or not sys.argv[1].isdigit():
        sys.exit("usage: python3 check_schedule.py <season> [orders.json]")
    season = int(sys.argv[1])
    path = sys.argv[2] if len(sys.argv) == 3 else os.path.join(HERE, "seasons", f"s{season}", "schedule_orders.json")
    T = json.load(open(path))
    A = json.load(open(os.path.join(HERE, "archive", f"s{season - 1}_final.json")))["data"]
    conf = json.load(open(os.path.join(HERE, "conf_pods.json")))
    league = json.load(open(os.path.join(HERE, "seasons", f"s{season}", "league_pods.json")))
    REG = {t["name"]: t["region"] for t in A["DATA"]["teams"]}
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    day = {"R": [], "L": []}
    for w in sorted(WEEKS):
        for i, k in enumerate(WEEKS[w]):
            if k != "-":
                day[k].append(w * 7 + (1, 3, 5)[i])
    check(len(day["R"]) == 15 and len(day["L"]) == 15, "calendar does not have 15 R and 15 L")

    def games_by_pair(group):
        gp = {}
        for n, games in group["games"].items():
            for h, a in games:
                check(frozenset((h, a)) not in gp, f"pair {h} v {a} meets twice")
                gp[frozenset((h, a))] = (int(n), h, a)
        return gp

    def run_ok(group, label):                                                         # rule 2
        teams = {t for g in group["games"].values() for p in g for t in p}
        for t in teams:
            seq = ""
            for n in range(1, 16):
                hs = [h for h, a in group["games"][str(n)] if t in (h, a)]
                check(len(hs) == 1, f"{label}: {t} plays {len(hs)} times in game {n}")
                seq += "H" if hs and hs[0] == t else "A"
            check("HHH" not in seq and "AAA" not in seq, f"{label}: {t} has 3 in a row ({seq})")

    def is_rivalry(games, pods):
        pos = {t: (p, i + 1) for p, ts in pods.items() for i, t in enumerate(ts)}
        return all(pos[h][0] == pos[a][0] and {pos[h][1], pos[a][1]} in ({1, 2}, {3, 4}) for h, a in games)

    prev_reg = A["SCHEDULE_DATA"]["regional"]
    reg_game = {}
    check(sorted(T["regions"]) == sorted(conf), "regions do not match conf_pods.json")
    for region, g in T["regions"].items():
        check(sorted(g["order"]) == list(range(1, 16)), f"{region}: order not a permutation")
        gp = games_by_pair(g)
        check(len(gp) == 120, f"{region}: {len(gp)} pairs")
        # every regional game is last season's game with home and away swapped
        prev = {frozenset((x["home"], x["away"])): x["home"] for rnd in prev_reg[region].values() for x in rnd}
        check(all(prev.get(p) == a for p, (_, h, a) in gp.items()), f"{region}: a host is not reversed from S{season - 1}")
        pods = {k: v["teams"] for k, v in conf[region].items()}
        check(is_rivalry(g["games"]["15"], pods), f"{region}: game 15 is not Rivalry Week")        # rule 1
        run_ok(g, region)
        rt = A["RT_DATA"][region]                                                                  # rule 3
        f = rt["8"][0]
        check(gp[frozenset((f["home"], f["away"]))][0] >= 8, f"{region}: RT finalists meet before game 8")
        for m in rt["6"]:
            check(gp[frozenset((m["home"], m["away"]))][0] >= 4, f"{region}: RT semifinal pair meets before game 4")
        for p, (n, _, _) in gp.items():
            reg_game[p] = n

    top3 = [t["name"] for t in A["HOF_DATA"]["plaques"]["league"]["seasons"][str(season - 1)]["teams"][:3]]
    seen, unpinned = [], {}
    check(sorted(T["divisions"], key=int) == [str(d) for d in range(1, 11)], "divisions are not 1-10")
    for d, g in T["divisions"].items():
        check(g["pods"] == {k: v["teams"] for k, v in league[d].items()},
              f"Div {d}: pods differ from league_pods.json")
        check(sorted(g["order"]) == list(range(1, 16)), f"Div {d}: order not a permutation")
        gp = games_by_pair(g)
        check(len(gp) == 120, f"Div {d}: {len(gp)} pairs")
        seen += [t for ts in g["pods"].values() for t in ts]
        run_ok(g, f"Div {d}")
        if d == "1":
            for a, b in itertools.combinations(top3, 2):                                           # rule 4
                check(gp[frozenset((a, b))][0] >= 9, f"Div 1: {a} v {b} meet before game 9")
        for p, (n, h, a) in gp.items():
            if REG[h] == REG[a]:                                                                    # rule 5
                check(abs(day["L"][n - 1] - day["R"][reg_game[p] - 1]) > 7,
                      f"Div {d}: {h} v {a} League game {n} within a week of Regional game {reg_game[p]}")
        # rules 6/7, derived independently: find the pod 1v2/3v4 games, decide if L1 must hold them
        rivalry = [p for p, (n, h, a) in gp.items() if is_rivalry([(h, a)], g["pods"])]
        check(len(rivalry) == 8, f"Div {d}: {len(rivalry)} pod 1v2/3v4 games")
        excused = [p for p in rivalry if REG[min(p)] == REG[max(p)]
                   and abs(day["L"][0] - day["R"][reg_game[p] - 1]) <= 7]
        excused += [p for p in rivalry if d == "1" and p <= set(top3)]
        should_pin = not excused
        check(g["l1_pinned"] == should_pin, f"Div {d}: pin flag {g['l1_pinned']} but should be {should_pin}")
        check(bool(g["unpinned_because"]) == (not should_pin), f"Div {d}: unpinned reasons do not match the pin")
        if should_pin:
            check(is_rivalry(g["games"]["1"], g["pods"]), f"Div {d}: L1 is not the pod 1v2/3v4 round")
        else:
            unpinned[d] = excused
    check(sorted(seen) == sorted(REG), "divisions do not hold all 160 teams exactly once")
    check(len({tuple(g["order"]) for g in T["regions"].values()}) == 10, "two regions share an order")
    check(len({tuple(g["order"]) for g in T["divisions"].values()}) == 10, "two divisions share an order")
    print("FAILURES:", len(fails))
    for f in fails[:30]:
        print("  -", f)
    print("unpinned divisions:", unpinned or "none")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
