"""Independent checker for the S10 trial draw. Does not use the draw code."""
import json, itertools, sys
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
T = json.load(open(sys.argv[1]))
A = json.load(open(ROOT + "/archive/s9_final.json"))["data"]
REG = {t["name"]: t["region"] for t in A["DATA"]["teams"]}
fails = []
def check(cond, msg):
    if not cond: fails.append(msg)

# S10 calendar, typed from SEASON10_PLAN B10 (independent of the draw script)
weeks = {1: ["", "", "R"], 2: ["", "", "R"], 3: ["", "", "R"], 4: ["", "", "L"], 5: ["", "", "R"], 6: ["L", "L", "R"]}
for w in range(7, 23):
    weeks[w] = {7: ["", "", "L"], 8: ["", "", "R"], 9: ["L", "L", "R"], 10: ["", "", "L"], 11: ["", "", "R"],
                12: ["L", "L", "R"], 13: ["", "", "L"], 14: ["", "", "R"], 15: ["", "", "L"], 16: ["", "", "R"],
                17: ["L", "L", "R"], 18: ["", "", "L"], 19: ["", "", "R"], 20: ["", "", "L"], 21: ["", "", "R"],
                22: ["", "", "R"]}[w]
day = {"R": [], "L": []}
for w in sorted(weeks):
    for i, k in enumerate(weeks[w]):
        if k: day[k].append(w * 7 + (1, 3, 5)[i])
check(len(day["R"]) == 15 and len(day["L"]) == 15, "calendar does not have 15 R and 15 L")

def games_by_pair(group):
    gp = {}
    for n, games in group["games"].items():
        for h, a in games:
            check(frozenset((h, a)) not in gp, f"pair {h} v {a} meets twice")
            gp[frozenset((h, a))] = (int(n), h, a)
    return gp

def run_ok(group, label):
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

conf = json.load(open(ROOT + "/conf_pods.json"))
s9reg = A["SCHEDULE_DATA"]["regional"]
reg_game = {}
for region, g in T["regions"].items():
    check(sorted(g["order"]) == list(range(1, 16)), f"{region}: order not a permutation")
    gp = games_by_pair(g); check(len(gp) == 120, f"{region}: {len(gp)} pairs")
    # hosts: every S10 regional game is the S9 game with home and away swapped
    s9 = {frozenset((x["home"], x["away"])): x["home"] for rnd in s9reg[region].values() for x in rnd}
    check(all(s9[p] == a for p, (_, h, a) in gp.items()), f"{region}: a host is not reversed from S9")
    pods = {k: v["teams"] for k, v in conf[region].items()}
    check(is_rivalry(g["games"]["15"], pods), f"{region}: game 15 is not Rivalry Week")        # rule 1
    run_ok(g, region)                                                                          # rule 2
    rt = A["RT_DATA"][region]
    f = rt["8"][0]; check(gp[frozenset((f["home"], f["away"]))][0] >= 8, f"{region}: RT finalists meet before game 8")
    for m in rt["6"]:
        check(gp[frozenset((m["home"], m["away"]))][0] >= 4, f"{region}: RT semifinal pair meets before game 4")
    for p, (n, _, _) in gp.items(): reg_game[p] = n

top3 = [t["name"] for t in A["HOF_DATA"]["plaques"]["league"]["seasons"]["9"]["teams"][:3]]
seen = []
unpinned = {}
for d, g in T["divisions"].items():
    check(sorted(g["order"]) == list(range(1, 16)), f"Div {d}: order not a permutation")
    gp = games_by_pair(g); check(len(gp) == 120, f"Div {d}: {len(gp)} pairs")
    seen += [t for ts in g["pods"].values() for t in ts]
    run_ok(g, f"Div {d}")                                                                       # rule 2
    if d == "1":
        for a, b in itertools.combinations(top3, 2):
            check(gp[frozenset((a, b))][0] >= 9, f"Div 1: {a} v {b} meet before game 9")         # rule 4
    for p, (n, h, a) in gp.items():
        if REG[h] == REG[a]:                                                                    # rule 5
            check(abs(day["L"][n - 1] - day["R"][reg_game[p] - 1]) > 7, f"Div {d}: {h} v {a} League game {n} within a week of Regional game {reg_game[p]}")
    # rules 6/7, derived independently: find the pod 1v2/3v4 games, decide if L1 must hold them
    rivalry = [p for p, (n, h, a) in gp.items() if is_rivalry([(h, a)], g["pods"])]
    check(len(rivalry) == 8, f"Div {d}: {len(rivalry)} pod 1v2/3v4 games")
    excused = [p for p in rivalry if REG[min(p)] == REG[max(p)] and abs(day["L"][0] - day["R"][reg_game[p] - 1]) <= 7]
    excused += [p for p in rivalry if d == "1" and p <= set(top3)]
    should_pin = not excused
    check(g["l1_pinned"] == should_pin, f"Div {d}: pin flag {g['l1_pinned']} but should be {should_pin}")
    check(is_rivalry(g["games"]["1"], g["pods"]) == should_pin or not should_pin, f"Div {d}: L1 is not the pod 1v2/3v4 round")
    if not should_pin: unpinned[d] = excused
check(sorted(seen) == sorted(REG), "divisions do not hold all 160 teams exactly once")
check(len({tuple(g["order"]) for g in T["regions"].values()}) == 10, "two regions share an order")
check(len({tuple(g["order"]) for g in T["divisions"].values()}) == 10, "two divisions share an order")
print("FAILURES:", len(fails)); [print("  -", f) for f in fails[:30]]
print("unpinned divisions:", unpinned or "none")
