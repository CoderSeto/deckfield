"""Trial draw (NOT the official one): Regional orders first, then League with
the D1 rule and the same-region one-week rule. Uniform over valid orders."""
import json, sys, random, itertools
from functools import lru_cache
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
import deckfield_ratings as db
FLIP = {'L': 'R', 'R': 'L', 'U': 'D', 'D': 'U'}
db.POD_ROUND_DEFS = {r: [(h, a) for a, h in p] for r, p in db.POD_ROUND_DEFS.items()}
db.CROSS_ROUND_DEFS = {r: (rel, pat, FLIP[d]) for r, (rel, pat, d) in db.CROSS_ROUND_DEFS.items()}
arch = json.load(open(ROOT + "/archive/s9_final.json"))["data"]
REG = {t["name"]: t["region"] for t in arch["DATA"]["teams"]}

# S10 calendar (SEASON10_PLAN B10): which week/day each R n / L n falls on
CAL = [(1,"Weekend","R"),(2,"Weekend","R"),(3,"Weekend","R"),(4,"Weekend","L"),(5,"Weekend","R"),
       (6,"Tue","L"),(6,"Thu","L"),(6,"Weekend","R"),(7,"Weekend","L"),(8,"Weekend","R"),
       (9,"Tue","L"),(9,"Thu","L"),(9,"Weekend","R"),(10,"Weekend","L"),(11,"Weekend","R"),
       (12,"Tue","L"),(12,"Thu","L"),(12,"Weekend","R"),(13,"Weekend","L"),(14,"Weekend","R"),
       (15,"Weekend","L"),(16,"Weekend","R"),(17,"Tue","L"),(17,"Thu","L"),(17,"Weekend","R"),
       (18,"Weekend","L"),(19,"Weekend","R"),(20,"Weekend","L"),(21,"Weekend","R"),(22,"Weekend","R")]
DOW = {"Tue": 1, "Thu": 3, "Weekend": 5}
DAY = {"R": {}, "L": {}}
for w, d, k in CAL: DAY[k][len(DAY[k]) + 1] = w * 7 + DOW[d]
assert len(DAY["R"]) == 15 and len(DAY["L"]) == 15
def blocked_L(r_game):            # League game numbers within 7 days of Regional game r_game
    return {n for n, day in DAY["L"].items() if abs(day - DAY["R"][r_game]) <= 7}

def meet(s, x, y): return next(r for r, g in s.items() for h, a in g if {h, a} == {x, y})

def solver(sched, forbid, pin15):
    """forbid: {round_def: set of disallowed game numbers}. Returns (count, sample(rng))."""
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
            if mask >> i & 1 or k in forbid.get(r, ()): continue
            if a is not None and bad(a, b, r): continue
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
                if x < c: break
                x -= c
            mask, a, b = mask | 1 << i, b, r; out.append(r)
        return out + ([15] if pin15 else [])
    return total, sample

rng = random.Random(10)
reg_game = {}                     # frozenset(pair) -> Regional game number
print("REGIONAL")
for region, pods in json.load(open(ROOT + "/conf_pods.json")).items():
    s = db.generate_pod_schedule({k: v["teams"] for k, v in pods.items()})
    rt = arch["RT_DATA"][region]
    forbid = {}
    for (x, y), first in [((rt["8"][0]["home"], rt["8"][0]["away"]), 8)] + [((m["home"], m["away"]), 4) for m in rt["6"]]:
        r = meet(s, x, y)
        if r != 15: forbid.setdefault(r, set()).update(range(1, first))
    n, sample = solver(s, forbid, True)
    order = sample(rng)
    pos = {r: i + 1 for i, r in enumerate(order)}
    for r, games in s.items():
        for h, a in games: reg_game[frozenset((h, a))] = pos[r]
    print(f"  {region:10} {n:>12,} valid  drew {order}")

pr = arch["PROMO_RELEGATION"]; new = {}
for div in range(1, 11):
    o = db.division_standings_seeds(9, div)
    for p in range(1, 17):
        m = pr[str(div)][p - 1] or ""
        new.setdefault(div - m.count("P") + m.count("R"), []).append((div, p, o[p]))
layout = {"UL": (1, 8, 9, 16), "UR": (2, 7, 10, 15), "LL": (4, 5, 12, 13), "LR": (3, 6, 11, 14)}
top3 = [db.division_standings_seeds(9, 1)[i] for i in (1, 2, 3)]
print("LEAGUE")
for d in range(1, 11):
    names = [n for _, _, n in sorted(new[d])]
    s = db.generate_pod_schedule({k: [names[i - 1] for i in v] for k, v in layout.items()})
    forbid, npairs = {}, 0
    for x, y in itertools.combinations(names, 2):
        if REG[x] == REG[y]:
            npairs += 1
            forbid.setdefault(meet(s, x, y), set()).update(blocked_L(reg_game[frozenset((x, y))]))
    base, _ = solver(s, {}, False)
    if d == 1:
        for i, a in enumerate(top3):
            for b in top3[i + 1:]: forbid.setdefault(meet(s, a, b), set()).update(range(1, 9))
    n, sample = solver(s, forbid, False)
    order = sample(rng) if n else None
    print(f"  Div {d:2}: {npairs:2} same-region pairs  {n:>12,} valid of {base:,}  drew {order}")

print("\nWindow check, R11:", sorted(blocked_L(11)))
for r in (1, 5, 15): print(f"  R{r}: blocks L{sorted(blocked_L(r))}")
