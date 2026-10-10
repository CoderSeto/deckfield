"""Exact count of round orders with max 2 home/away in a row, plus the
RT / Division 1 protections. Read-only prototype."""
import json, sys, math
from collections import defaultdict
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
import deckfield_ratings as db
FLIP = {'L': 'R', 'R': 'L', 'U': 'D', 'D': 'U'}
db.POD_ROUND_DEFS = {r: [(h, a) for a, h in p] for r, p in db.POD_ROUND_DEFS.items()}
db.CROSS_ROUND_DEFS = {r: (rel, pat, FLIP[d]) for r, (rel, pat, d) in db.CROSS_ROUND_DEFS.items()}
arch = json.load(open(ROOT + "/archive/s9_final.json"))["data"]

def meet(s, x, y): return next(r for r, g in s.items() for h, a in g if {h, a} == {x, y})

def count(sched, minpos, pin15, cap=2):
    teams = sorted({t for g in sched.values() for p in g for t in p})
    hv = {r: tuple(t in {h for h, _ in sched[r]} for t in teams) for r in sched}
    def bad(a, b, c): return any(x == y == z for x, y, z in zip(hv[a], hv[b], hv[c]))
    R = list(range(1, 15)) if pin15 else list(range(1, 16))
    layer = {(0, None, None): 1}
    for k in range(1, len(R) + 1):
        nxt = defaultdict(int)
        for (mask, a, b), n in layer.items():
            for i, r in enumerate(R):
                if mask >> i & 1 or k < minpos.get(r, 1): continue
                if a is not None and bad(a, b, r): continue
                nxt[(mask | 1 << i, b, r)] += n
        layer = nxt
    if pin15:
        return sum(n for (m, a, b), n in layer.items() if not bad(a, b, 15)), math.factorial(14)
    return sum(layer.values()), math.factorial(15)

def report(name, sched, avoid, pin15):
    base, total = count(sched, {}, pin15)
    mp = {}
    for (x, y), first in avoid:
        r = meet(sched, x, y); mp[r] = max(mp.get(r, 1), first)
    if pin15: mp.pop(15, None)           # Rivalry Week is always game 15
    ruled, _ = count(sched, mp, pin15)
    print(f"{name:10} max-2 orders: {base:>10,} ({base/total:.4%})   with protections: {ruled:>10,}")

for region, pods in json.load(open(ROOT + "/conf_pods.json")).items():
    s = db.generate_pod_schedule({k: v["teams"] for k, v in pods.items()})
    rt = arch["RT_DATA"][region]
    avoid = [((rt["8"][0]["home"], rt["8"][0]["away"]), 8)] + [((g["home"], g["away"]), 4) for g in rt["6"]]
    report(region, s, avoid, True)

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
    s = db.generate_pod_schedule({k: [names[i - 1] for i in v] for k, v in layout.items()})
    avoid = [((a, b), 9) for i, a in enumerate(top3) for b in top3[i + 1:]] if d == 1 else []
    report(f"Div {d}", s, avoid, False)
