"""S10 TRIAL schedule draw (all seven rules). Writes schedule_orders_s10_trial.json."""
import json, sys, random, itertools
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
src = open(__file__.replace("draw_s10.py", "draw.py")).read().split("rng = random.Random(10)")[0]
exec(src)                                      # Outline B tables, calendar, solver, meet()

def label(r):
    if r in db.POD_ROUND_DEFS:
        return "Pod " + ", ".join(f"{a} at {h}" for a, h in db.POD_ROUND_DEFS[r])
    rel, pat, d = db.CROSS_ROUND_DEFS[r]
    pats = "1v1" if pat == "same" else "/".join(f"{a}v{b}" for a, b in pat)
    return f"{rel} {pats} at {d}"

rng = random.Random(10)
out = {"season": 10, "seed": 10, "outline": "B", "round_defs": {r: label(r) for r in range(1, 16)},
       "regions": {}, "divisions": {}}
reg_game = {}
for region, pods in json.load(open(ROOT + "/conf_pods.json")).items():
    s = db.generate_pod_schedule({k: v["teams"] for k, v in pods.items()})
    rt = arch["RT_DATA"][region]; forbid = {}
    for (x, y), first in [((rt["8"][0]["home"], rt["8"][0]["away"]), 8)] + [((m["home"], m["away"]), 4) for m in rt["6"]]:
        r = meet(s, x, y)
        if r != 15: forbid.setdefault(r, set()).update(range(1, first))
    n, sample = solver(s, forbid, True); order = sample(rng)
    pos = {r: i + 1 for i, r in enumerate(order)}
    for r, games in s.items():
        for h, a in games: reg_game[frozenset((h, a))] = pos[r]
    out["regions"][region] = {"valid_orders": n, "order": order,
                              "games": {str(i + 1): s[r] for i, r in enumerate(order)}}

pr = arch["PROMO_RELEGATION"]; new = {}
for div in range(1, 11):
    o = db.division_standings_seeds(9, div)
    for p in range(1, 17):
        m = pr[str(div)][p - 1] or ""
        new.setdefault(div - m.count("P") + m.count("R"), []).append((div, p, o[p]))
layout = {"UL": (1, 8, 9, 16), "UR": (2, 7, 10, 15), "LL": (4, 5, 12, 13), "LR": (3, 6, 11, 14)}
top3 = [db.division_standings_seeds(9, 1)[i] for i in (1, 2, 3)]
for d in range(1, 11):
    names = [n for _, _, n in sorted(new[d])]
    pods = {k: [names[i - 1] for i in v] for k, v in layout.items()}
    s = db.generate_pod_schedule(pods)
    reasons = []
    if d == 1:
        reasons += [f"D1 top three {a} v {b} meet in it" for a, b in itertools.combinations(top3, 2) if meet(s, a, b) == 15]
    reasons += [f"{x} v {y} (same region) play Regional game {reg_game[frozenset((x, y))]}, within 7 days of L1"
                for x, y in s[15] if REG[x] == REG[y] and 1 in blocked_L(reg_game[frozenset((x, y))])]
    forbid = {}
    for x, y in itertools.combinations(names, 2):
        if REG[x] == REG[y]: forbid.setdefault(meet(s, x, y), set()).update(blocked_L(reg_game[frozenset((x, y))]))
    if d == 1:
        for a, b in itertools.combinations(top3, 2): forbid.setdefault(meet(s, a, b), set()).update(range(1, 9))
    if not reasons: forbid[15] = set(forbid.get(15, set())) | set(range(2, 16))
    n, sample = solver(s, forbid, False); order = sample(rng)
    out["divisions"][str(d)] = {"valid_orders": n, "order": order, "l1_pinned": not reasons,
                                "unpinned_because": reasons, "pods": pods,
                                "games": {str(i + 1): s[r] for i, r in enumerate(order)}}
json.dump(out, open(__file__.replace("draw_s10.py", "schedule_orders_s10_trial.json"), "w"), indent=1)
print("written")
