"""The all-time database and dashboard, Seasons 1-9, built from history/.

    python3 build_alltime.py

Writes alltime.db (SQLite, gitignored -- rebuilt from history/ like
deckfield.db is from results/) and alltime_dashboard.html (a static page,
separate from the season dashboard, every number inline).

Everything here is computed from the archive as recorded; nothing is
estimated. Two rules shape the numbers:

- Scoring records are split into two eras, S1-S6 and S7-S9: S7 changed the
  scoring system fundamentally ("Base-2 Scoring System") and S8/S9 adjusted it
  again, so points are not comparable across that line (per the user,
  2026-10-11). Records, Elo and titles run across all nine seasons -- Elo is
  one unbroken, checked chain from S1's 1800 to S9.
- Game types differ by era, so records are split into classes that mean the
  same thing everywhere: Regional (games within a team's conference/region),
  League (S5-S9 leagues; S3-S4's MRL/UTL), Interconference (S1-S4), Cup (the
  WCS, the Grand Swiss, the PA and RDS cups, S3's conference cups),
  Playoffs (P) and Finals (F). Byes (walkovers) are not games and count in
  no record.
"""
import csv
import json
import os
import sqlite3
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(ROOT, "history")
SEASONS = range(1, 10)
DB_PATH = os.path.join(ROOT, "alltime.db")
PAGE_PATH = os.path.join(ROOT, "alltime_dashboard.html")

CLASS_OF = {"R": "Regional", "L": "League", "M": "League", "I": "Interconference",
            "S": "Cup", "W": "Cup", "C": "Cup", "P": "Playoffs", "F": "Finals"}
CLASSES = ["Regional", "League", "Interconference", "Cup", "Playoffs", "Finals"]
RENAMED_SINCE = {"Resort Area": "Battle Zone", "Fight Area": "Cabo Poco", "South Lakes": "Wild Area",
                 "South Kalos": "Blueberry Terarium", "East Paldea": "Mossui Town"}


def era(season):
    return "S1-S6" if season <= 6 else "S7-S9"


def num(x):
    if x in (None, ""):
        return None
    f = float(x)
    return int(f) if f.is_integer() else f


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load():
    ident = json.load(open(os.path.join(HISTORY, "teams.json")))
    names = {int(k): v["s9_name"] for k, v in ident.items()}
    by_name = {v: k for k, v in names.items()}
    teams_s9 = {int(r["team"]): r for r in csv.DictReader(open(os.path.join(HISTORY, "s9", "teams.csv")))}
    seasons, games, team_seasons, events = {}, [], [], []
    for s in SEASONS:
        meta = json.load(open(os.path.join(HISTORY, f"s{s}", "events.json")))
        evs = meta["events"]
        ev_index = {}
        for e in evs:
            ev_index[e["code"]] = len(events)
            title = e["title"]
            if title.isupper():
                # The sheets wrote some titles in capitals ("LEAGUE MATCHDAY
                # TWELVE"); show them like the rest.
                title = " ".join(w if w in ("WCS", "MRL", "UTL", "MGGG", "LPO") else w.capitalize()
                                 for w in title.split())
            if e.get("sub"):
                title = f"{title} - {e['sub']}"
            events.append({"season": s, "code": e["code"], "title": title, "round": e["round"],
                           "games": e["games"]})
        for r in csv.DictReader(open(os.path.join(HISTORY, f"s{s}", "teams.csv"))):
            team_seasons.append({
                "season": s, "team": int(r["team"]), "name": r["name"], "region": r["region"],
                "division": r["division"], "opening_elo": num(r["opening_elo"]),
                "final_elo": num(r["final_elo"]), "final_rank": num(r["final_rank"]),
            })
        rows = list(csv.DictReader(open(os.path.join(HISTORY, f"s{s}", "games.csv"))))
        for r in rows:
            a, b = int(r["team_a"]), int(r["team_b"])
            a_won = r["winner"] == "A"
            w, l = (a, b) if a_won else (b, a)
            pick = (lambda x, y: (x, y)) if a_won else (lambda x, y: (y, x))
            wp, lp = pick(int(r["pf_a"]), int(r["pa_a"]))
            wg, lg = pick(num(r["goals_a"]), num(r["goals_b"]))
            we, le = pick(num(r["elo_a_after"]), num(r["elo_b_after"]))
            k = num(r["elo_k"])
            host = None
            if r["home"] == "A":
                host = a
            games.append({
                "season": s, "era": era(s), "seq": int(r["seq"]), "event": ev_index[r["event"]],
                "event_code": r["event"], "game_type": r["game_type"], "cls": CLASS_OF[r["game_type"]],
                "winner": w, "loser": l, "w_pts": wp, "l_pts": lp, "w_goals": wg, "l_goals": lg,
                "host": host, "host_source": r["host_source"], "ot": int(r["ot"]),
                "w_elo_after": we, "l_elo_after": le, "k": k,
                "w_elo_before": we - k if we is not None and k is not None else None,
                "l_elo_before": le + k if le is not None and k is not None else None,
            })
        seasons[s] = {"season": s, "era": era(s), "teams": sum(1 for t in team_seasons if t["season"] == s),
                      "games": len(rows)}
    return names, by_name, teams_s9, seasons, games, team_seasons, events


def canonical(name, by_name):
    n = name.rstrip("*")
    return by_name.get(RENAMED_SINCE.get(n, n))


def load_titles(by_name, games, events):
    """Honours from the Hall of Fame, plus S3's seven conference cups (not
    on a plaque; their finals are in the games)."""
    hof = json.load(open(os.path.join(ROOT, "hall_of_fame.json")))
    titles = []

    def add(season, kind, label, name, place):
        t = canonical(name, by_name)
        if t is None:
            raise ValueError(f"Hall of Fame name {name!r} has no canonical team")
        titles.append({"season": season, "kind": kind, "label": label, "team": t, "place": place})

    for s, p in hof["plaques"]["world"]["seasons"].items():
        add(int(s), "World", p["labels"][0], p["teams"][0]["name"], 1)
        add(int(s), "World", p["labels"][1], p["teams"][1]["name"], 2)
    for s, p in hof["plaques"]["cup"]["seasons"].items():
        add(int(s), "Cup", p["labels"][0], p["teams"][0]["name"], 1)
        if p["teams"][1]:
            add(int(s), "Cup", p["labels"][1], p["teams"][1]["name"], 2)
    for s, p in hof["plaques"]["rds"]["seasons"].items():
        for lab, t in zip(p["labels"], p["teams"]):
            add(int(s), "Regional Cup", lab.replace(" Winner", ""), t["name"], 1)
    for s, p in hof["plaques"]["league"]["seasons"].items():
        for lab, t in zip(p["labels"], p["teams"]):
            if t is None:
                continue
            if lab == "UTL Champion B" and int(s) <= 4:
                # S3-S4 had one UTL: the plaque's "Champion B" is its runner-up
                # (S3's final: Resort Area over Olivine City). S5-S6 had two
                # Ultra divisions, A and B, and two genuine champions.
                place, tier = 2, 2
            elif "Champion" not in lab:
                place = 2 if "Second" in lab else 3
                tier = 1
            else:
                place = 1
                tier = 1 if ("MRL" in lab or "First" in lab or "Master" in lab) else 2
            add(int(s), "League" if tier == 1 else "League (2nd tier)", lab, t["name"], place)
    for region, plates in hof["regions"].items():
        for s, t in plates.items():
            if t:
                add(int(s), "Regional", f"{region} champion", t["name"], 1)
    for s, row in hof["placings"].items():
        for i, t in enumerate(row, start=3):
            if t:
                add(int(s), "World placing", f"No. {i}", t["name"], i)
    # S3's conference cups: each cup's two-legged final, decided on aggregate
    # (none was level).
    cups = {"CRY": "Crystal", "EME": "Emerald", "PLA": "Platinum", "GRA": "Gray",
            "PRI": "Prism", "KAH": "Kahuna", "GIG": "Gigantamax"}
    for code, name in cups.items():
        legs = [g for g in games if g["season"] == 3 and g["event_code"] in (f"{code}-F-1", f"{code}-F-2")]
        agg = Counter()
        for g in legs:
            agg[g["winner"]] += g["w_pts"]
            agg[g["loser"]] += g["l_pts"]
        (champ, cp), (ru, rp) = agg.most_common(2)
        if cp == rp:
            raise ValueError(f"S3 {name} Cup final level on aggregate")
        titles.append({"season": 3, "kind": "Regional Cup", "label": f"{name} Cup", "team": champ, "place": 1})
    return titles


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

def team_stats(names, games, team_seasons, titles):
    rec = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    pts = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))    # era -> [pf, pa, games]
    peak, low = {}, {}
    for g in games:
        for t, won in ((g["winner"], True), (g["loser"], False)):
            i = 0 if won else 1
            rec[t]["Overall"][i] += 1
            rec[t][g["cls"]][i] += 1
            if g["cls"] in ("Playoffs", "Finals"):
                rec[t]["Postseason"][i] += 1
            if g["host"] is not None:
                rec[t]["Home" if g["host"] == t else "Away"][i] += 1
            if g["ot"]:
                rec[t]["OT"][i] += 1
            p = pts[t][g["era"]]
            p[0] += g["w_pts"] if won else g["l_pts"]
            p[1] += g["l_pts"] if won else g["w_pts"]
            p[2] += 1
            e = g["w_elo_after"] if won else g["l_elo_after"]
            if e is not None:
                if t not in peak or e > peak[t][0]:
                    peak[t] = (e, g["season"], g["event"])
                if t not in low or e < low[t][0]:
                    low[t] = (e, g["season"], g["event"])
    honours = defaultdict(Counter)
    for x in titles:
        if x["place"] == 1:
            honours[x["team"]][x["kind"]] += 1
        elif x["kind"] == "World" and x["place"] == 2:
            honours[x["team"]]["World final"] += 1
    seasons_of = defaultdict(list)
    for ts in team_seasons:
        seasons_of[ts["team"]].append(ts["season"])
    s9 = {ts["team"]: ts for ts in team_seasons if ts["season"] == 9}
    out = []
    for t in sorted(names):
        r = rec[t]
        out.append({
            "team": t, "name": names[t], "region": s9[t]["region"], "seasons": sorted(seasons_of[t]),
            "records": {k: list(v) for k, v in r.items()},
            "points": {k: list(v) for k, v in pts[t].items()},
            "peak": list(peak[t]) if t in peak else None, "low": list(low[t]) if t in low else None,
            "elo_s9": s9[t]["final_elo"], "honours": dict(honours[t]),
            "best_rank": min((ts["final_rank"] for ts in team_seasons if ts["team"] == t
                              and isinstance(ts["final_rank"], int)), default=None),
        })
    return out


def streaks(games):
    """Each team's longest winning and losing run, in play order, across
    season boundaries."""
    seq = defaultdict(list)
    for g in sorted(games, key=lambda g: (g["season"], g["seq"])):
        seq[g["winner"]].append((True, g["season"], g["event"]))
        seq[g["loser"]].append((False, g["season"], g["event"]))
    best = []
    for t, rs in seq.items():
        for want in (True, False):
            run, start, top = 0, None, (0, None, None)
            for i, (won, s, e) in enumerate(rs):
                if won == want:
                    if run == 0:
                        start = (s, e)
                    run += 1
                    if run > top[0]:
                        top = (run, start, (s, e))
                else:
                    run = 0
            best.append({"team": t, "kind": "W" if want else "L", "length": top[0],
                         "from": top[1], "to": top[2]})
    return best


def records(games, team_seasons):
    """Single-game and season records. Scoring is split by era."""
    out = {"game": {}, "season": {}, "elo": {}}
    for e in ("S1-S6", "S7-S9"):
        gs = [g for g in games if g["era"] == e]
        pick = lambda key, n=15: sorted(gs, key=key, reverse=True)[:n]  # noqa: E731
        out["game"][e] = {
            "score": [compact(g) for g in pick(lambda g: g["w_pts"])],
            "margin": [compact(g) for g in pick(lambda g: g["w_pts"] - g["l_pts"])],
            "combined": [compact(g) for g in pick(lambda g: g["w_pts"] + g["l_pts"])],
            "goals": [compact(g) for g in pick(lambda g: g["w_goals"] if g["w_goals"] is not None else -1)
                      if g["w_goals"] is not None],
        }
        # Season scoring: points per game for and against, at least 10 games.
        per = defaultdict(lambda: [0, 0, 0, 0])    # pf, pa, games, wins
        for g in gs:
            for t, won in ((g["winner"], True), (g["loser"], False)):
                p = per[(g["season"], t)]
                p[0] += g["w_pts"] if won else g["l_pts"]
                p[1] += g["l_pts"] if won else g["w_pts"]
                p[2] += 1
                p[3] += won
        rows = [{"season": s, "team": t, "pf": p[0], "pa": p[1], "games": p[2], "wins": p[3]}
                for (s, t), p in per.items() if p[2] >= 10]
        out["season"][e] = {
            "pf_per_game": sorted(rows, key=lambda r: r["pf"] / r["games"], reverse=True)[:15],
            "pd_per_game": sorted(rows, key=lambda r: (r["pf"] - r["pa"]) / r["games"], reverse=True)[:15],
        }
    # Season records that do not depend on scoring: all nine seasons.
    per = defaultdict(lambda: [0, 0])
    for g in games:
        per[(g["season"], g["winner"])][0] += 1
        per[(g["season"], g["loser"])][1] += 1
    rows = [{"season": s, "team": t, "wins": w, "losses": l} for (s, t), (w, l) in per.items()]
    out["season"]["all"] = {
        "win_pct": sorted([r for r in rows if r["wins"] + r["losses"] >= 10],
                          key=lambda r: (r["wins"] / (r["wins"] + r["losses"]), r["wins"]), reverse=True)[:20],
        "wins": sorted(rows, key=lambda r: r["wins"], reverse=True)[:15],
        # No team has gone a whole season unbeaten (every season ends in a
        # postseason), so: fewest losses over a full season.
        "fewest_losses": sorted([r for r in rows if r["wins"] + r["losses"] >= 15],
                                key=lambda r: (r["losses"], -r["wins"]))[:15],
    }
    # Elo: upsets (the winner's Elo before, below the loser's), and seasons.
    eg = [g for g in games if g["w_elo_before"] is not None]
    # Upsets need each side's Elo GOING IN (after less K). S5 applied Elo out
    # of game order and its per-game K does not add up to a team's season, so
    # its Elo-before is not reliable there: S5 is left out of upsets.
    out["elo"]["upsets"] = [compact(g) for g in sorted(
        (g for g in eg if g["season"] != 5), key=lambda g: g["w_elo_before"] - g["l_elo_before"])[:20]]
    out["elo"]["k"] = [compact(g) for g in sorted(eg, key=lambda g: -g["k"])[:15]]
    ts = [t for t in team_seasons if t["opening_elo"] is not None and t["final_elo"] is not None]
    out["elo"]["season_gain"] = sorted(
        [{"season": t["season"], "team": t["team"], "from": t["opening_elo"], "to": t["final_elo"]} for t in ts],
        key=lambda r: r["to"] - r["from"], reverse=True)[:15]
    out["elo"]["season_loss"] = sorted(
        [{"season": t["season"], "team": t["team"], "from": t["opening_elo"], "to": t["final_elo"]} for t in ts],
        key=lambda r: r["to"] - r["from"])[:15]
    return out


def compact(g):
    """A game as the dashboard keeps it."""
    return [g["season"], g["event"], g["winner"], g["loser"], g["w_pts"], g["l_pts"],
            0 if g["host"] is None else (1 if g["host"] == g["winner"] else 2), g["ot"],
            g["w_elo_after"], g["l_elo_after"], g["k"],
            g["w_goals"], g["l_goals"], CLASSES.index(g["cls"])]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

SCHEMA = """
DROP TABLE IF EXISTS teams; DROP TABLE IF EXISTS seasons; DROP TABLE IF EXISTS team_seasons;
DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS games; DROP TABLE IF EXISTS titles;
DROP VIEW IF EXISTS team_games;
CREATE TABLE teams (team INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT);
CREATE TABLE seasons (season INTEGER PRIMARY KEY, era TEXT, teams INTEGER, games INTEGER);
CREATE TABLE team_seasons (season INTEGER, team INTEGER, name TEXT, region TEXT, division TEXT,
    opening_elo REAL, final_elo REAL, final_rank INTEGER, PRIMARY KEY (season, team));
CREATE TABLE events (event INTEGER PRIMARY KEY, season INTEGER, code TEXT, title TEXT, round INTEGER,
    games INTEGER);
CREATE TABLE games (game INTEGER PRIMARY KEY, season INTEGER, era TEXT, seq INTEGER, event INTEGER,
    game_type TEXT, class TEXT, winner INTEGER, loser INTEGER, w_pts INTEGER, l_pts INTEGER,
    w_goals INTEGER, l_goals INTEGER, host INTEGER, host_source TEXT, ot INTEGER,
    w_elo_before REAL, w_elo_after REAL, l_elo_before REAL, l_elo_after REAL, elo_k REAL);
-- w/l_elo_before = Elo after less (plus) the game's K. Exact in every season
-- but S5, which applied Elo out of game order: there only Elo after is sure.
CREATE TABLE titles (season INTEGER, kind TEXT, label TEXT, team INTEGER, place INTEGER);
CREATE INDEX games_w ON games(winner); CREATE INDEX games_l ON games(loser);
CREATE VIEW team_games AS
    SELECT game, season, era, seq, event, class, winner AS team, loser AS opponent, 1 AS won,
           w_pts AS pf, l_pts AS pa, CASE WHEN host IS NULL THEN NULL WHEN host = winner THEN 1 ELSE 0 END AS home,
           ot, w_elo_before AS elo_before, w_elo_after AS elo_after FROM games
    UNION ALL
    SELECT game, season, era, seq, event, class, loser, winner, 0, l_pts, w_pts,
           CASE WHEN host IS NULL THEN NULL WHEN host = loser THEN 1 ELSE 0 END,
           ot, l_elo_before, l_elo_after FROM games;
"""


def check_elo_chain(team_seasons):
    """Refuse to build unless Elo is one unbroken chain: every season opens
    and closes at exactly 1800 per team, and each team opens a season on the
    Elo it closed the previous one with. A team new to the league enters on
    its preseason Elo (S3's and S4's new conferences, S5's Terastal), which
    only the season total constrains."""
    by = defaultdict(dict)
    for t in team_seasons:
        by[t["season"]][t["team"]] = t
    bad = []
    for s, rows in sorted(by.items()):
        for key in ("opening_elo", "final_elo"):
            total = sum(r[key] for r in rows.values())
            if total != 1800 * len(rows):
                bad.append(f"S{s} {key} totals {total}, not {1800 * len(rows)}")
        prev = by.get(s - 1, {})
        for team, r in rows.items():
            if team in prev and r["opening_elo"] != prev[team]["final_elo"]:
                bad.append(f"S{s} team {team} opens at {r['opening_elo']}, "
                           f"closed S{s - 1} at {prev[team]['final_elo']}")
    if bad:
        raise SystemExit("Elo chain broken:\n  " + "\n  ".join(bad[:20]))
    return sum(1 for s in by if s - 1 in by for t in by[s] if t in by[s - 1])


def write_db(names, teams_s9, seasons, games, team_seasons, events, titles):
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO teams VALUES (?,?,?)",
                  [(t, n, teams_s9[t]["region"]) for t, n in sorted(names.items())])
    c.executemany("INSERT INTO seasons VALUES (?,?,?,?)",
                  [(s["season"], s["era"], s["teams"], s["games"]) for s in seasons.values()])
    c.executemany("INSERT INTO team_seasons VALUES (?,?,?,?,?,?,?,?)",
                  [(t["season"], t["team"], t["name"], t["region"], str(t["division"]), t["opening_elo"],
                    t["final_elo"], t["final_rank"]) for t in team_seasons])
    c.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)",
                  [(i, e["season"], e["code"], e["title"], e["round"], e["games"]) for i, e in enumerate(events)])
    c.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [(i, g["season"], g["era"], g["seq"], g["event"], g["game_type"], g["cls"], g["winner"],
                    g["loser"], g["w_pts"], g["l_pts"], g["w_goals"], g["l_goals"], g["host"],
                    g["host_source"], g["ot"], g["w_elo_before"], g["w_elo_after"], g["l_elo_before"],
                    g["l_elo_after"], g["k"]) for i, g in enumerate(games)])
    c.executemany("INSERT INTO titles VALUES (?,?,?,?,?)",
                  [(x["season"], x["kind"], x["label"], x["team"], x["place"]) for x in titles])
    c.commit()
    c.close()


def write_page(payload):
    template = open(os.path.join(ROOT, "alltime_template.html")).read()
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    page = template.replace("/*__ALLTIME_DATA__*/null", blob)
    if page == template:
        raise SystemExit("alltime_template.html has no /*__ALLTIME_DATA__*/null placeholder")
    with open(PAGE_PATH, "w") as f:
        f.write(page)


def main():
    names, by_name, teams_s9, seasons, games, team_seasons, events = load()
    links = check_elo_chain(team_seasons)
    titles = load_titles(by_name, games, events)
    stats = team_stats(names, games, team_seasons, titles)
    frozen = json.load(open(os.path.join(ROOT, "archive", "s9_final.json")))["data"]
    payload = {
        "seasons": list(seasons.values()),
        "events": [[e["season"], e["code"], e["title"]] for e in events],
        "teams": stats,
        "team_seasons": [[t["season"], t["team"], t["name"], t["region"], t["division"], t["opening_elo"],
                          t["final_elo"], t["final_rank"]] for t in team_seasons],
        "games": [compact(g) for g in sorted(games, key=lambda g: (g["season"], g["seq"]))],
        "titles": [[x["season"], x["kind"], x["label"], x["team"], x["place"]] for x in titles],
        "streaks": streaks(games),
        "records": records(games, team_seasons),
        "classes": CLASSES,
        "region_colors": frozen["REGION_COLORS"], "region_display": frozen["REGION_DISPLAY"],
        "region_order": frozen["REGION_ORDER"],
    }
    write_db(names, teams_s9, seasons, games, team_seasons, events, titles)
    write_page(payload)
    print(f"all-time: {len(games)} games, {len(names)} teams, {len(titles)} honours, "
          f"{len(events)} events, {links} Elo season links checked -> {os.path.relpath(DB_PATH, ROOT)}, {os.path.relpath(PAGE_PATH, ROOT)}")


if __name__ == "__main__":
    main()
