"""Season 9 into history/s9/, in the archive's own format.

    python3 extract_history_s9.py <s9_audit.db>

Season 9 is frozen (archive/), but the frozen record keeps no game-level data
-- only each team's log of opponent, game type and result. The games
themselves come from Season 9's audit rebuild (the workbook plus results/s9/,
replayed as the runbook describes), which is the audit trail, not the
authority. So nothing is written unless the rebuild agrees with the frozen
record: every team's overall / regional / league / cup / playoff+finals / home
/ away record and its game log (archive/s9_final.json's DATA), its final Elo,
the game count, and the Hall of Fame's S9 champions. Its opening Elo must also
equal S8's archived final Elo, team by team -- the link that makes the Elo
chain run unbroken from S1 to S9.

Fields S9 never stored per game stay blank: goals scored (the CSV format from
round 12 on has none) and each side's rank going in. Every host is recorded
(host_team_id; S9's Archive H/A for rounds 1-11, the CSV's `home` after).
"""
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "history", "s9")
os.environ.setdefault("DECKFIELD_SEASON", "9")

import extract_history as eh  # noqa: E402  (GAME_FIELDS, write_csv, the identity map)

STAGE = {"Draw": "D", "Process": "P"}
WC_STAGE = {"Group": "G", "Play-in": "PI", "R16": "R16-", "QF": "QF-", "SF": "SF-", "Final": "F-"}


def event_code(key):
    """The engine's schedule key -> an archive event code and title."""
    kind = key[0]
    if kind in ("R", "L"):
        return f"{kind}{key[1]}", f"{'Regional' if kind == 'R' else 'League'} Matchday {key[1]}"
    if kind in ("RDS", "PA") and key[1] in STAGE:
        return f"{kind}-{STAGE[key[1]]}{key[2]}", f"{kind} Cup {key[1]} Round {key[2]}"
    if kind in ("RDS", "PA") and key[1] in ("SF", "Final") and len(key) == 4:
        leg = {"Tue": 1, "Thu": 2}[key[3]]
        return f"{kind}-{'SF' if key[1] == 'SF' else 'F'}-{leg}", f"{kind} Cup {key[1]}, Leg {leg}"
    if kind == "PA" and key[1] == "Final":
        return f"PA-F-{key[2]}", f"PA Cup Final, Game {key[2]}"
    if kind == "RT":
        return f"RT{key[1]}", f"Regional Tournament Matchday {key[1]}"
    if kind == "WC":
        return f"WC-{WC_STAGE[key[1]]}{key[2]}", f"World Championship {key[1]} {key[2]}"
    raise ValueError(f"unmapped schedule key {key}")


def record(rows):
    w = sum(1 for r in rows if r >= 2)
    return f"{w}-{len(rows) - w}"


def main(dbpath):
    import deckfield_ratings as engine
    frozen = json.load(open(os.path.join(ROOT, "archive", "s9_final.json")))["data"]
    inputs = json.load(open(os.path.join(ROOT, "archive", "s9_inputs.json")))["teams"]
    hof = json.load(open(os.path.join(ROOT, "hall_of_fame.json")))
    teams = {t["dex"]: t for t in frozen["DATA"]["teams"]}
    conn = sqlite3.connect(dbpath)
    conn.row_factory = sqlite3.Row
    games = conn.execute("SELECT * FROM games WHERE season = 9 ORDER BY round, game_id").fetchall()
    walkovers = conn.execute("SELECT * FROM walkovers WHERE season = 9 ORDER BY round, team_id").fetchall()
    rounds = {v: k for k, v in engine.full_schedule_abs_round_mapping().items()}
    errors = []

    # Events: every scheduled round, in order, empty ones included.
    events, ev_of_round = [], {}
    for n in sorted(rounds):
        code, title = event_code(rounds[n])
        ev = {"round": n, "code": code, "title": title, "games": 0}
        events.append(ev)
        ev_of_round[n] = ev

    # Opening Elo: S8's archived final Elo, by canonical id (= S9 dex).
    s8 = {int(r["team"]): float(r["final_elo"]) for r in csv.DictReader(
        open(os.path.join(ROOT, "history", "s8", "teams.csv")))}
    elo = dict(s8)
    rows, per_team = [], defaultdict(list)
    for seq, g in enumerate(games, start=1):
        a, b = g["team_a"], g["team_b"]
        host = g["host_team_id"]
        if host not in (a, b):
            errors.append(f"game {g['game_id']}: host {host} is neither side")
            continue
        flip = host != a
        h, v = (b, a) if flip else (a, b)
        res = (3 - g["result_a"]) if flip else g["result_a"]
        pf, pa = (g["pa_a"], g["pf_a"]) if flip else (g["pf_a"], g["pa_a"])
        eh_after = g["elo_b_after"] if flip else g["elo_a_after"]
        ev_after = g["elo_a_after"] if flip else g["elo_b_after"]
        k = abs(eh_after - elo[h])
        if abs(abs(ev_after - elo[v]) - k) > 0.5 or (eh_after - elo[h]) * (ev_after - elo[v]) > 0:
            errors.append(f"game {g['game_id']} (round {g['round']}): Elo does not mirror "
                          f"({elo[h]}->{eh_after}, {elo[v]}->{ev_after})")
        elo[h], elo[v] = eh_after, ev_after
        ev = ev_of_round[g["round"]]
        ev["games"] += 1

        def num(x, neg=False):
            if x is None:
                return ""
            x = -x if neg else x
            return int(x) if float(x).is_integer() else round(x, 4)
        rows.append({
            "seq": seq, "round": g["round"], "event": ev["code"], "day": "",
            "game_type": g["game_type"], "team_a": h, "team_b": v, "result_a": res,
            "pf_a": pf, "pa_a": pa,
            "raw_goal_a": num(g["raw_goal_score_b"] if flip else g["raw_goal_score_a"]),
            "raw_goal_b": num(g["raw_goal_score_a"] if flip else g["raw_goal_score_b"]),
            "spread_a": num(g["spread_a"], neg=flip), "interest": num(g["interest_score"]),
            "dscr_a": num(g["dscr_b"] if flip else g["dscr_a"]),
            "dscr_b": num(g["dscr_a"] if flip else g["dscr_b"]),
            "elo_a_after": num(eh_after), "elo_b_after": num(ev_after),
            "home": "A", "host_source": "recorded",
            "ex_a": num(g["ex_bonus_b"] if flip else g["ex_bonus_a"]),
            "ex_b": num(g["ex_bonus_a"] if flip else g["ex_bonus_b"]),
            "ex_winner_diff": "", "winner": "A" if res >= 2 else "B",
            "goals_a": "", "goals_b": "", "aggot_a": "", "aggot_b": "",
            "elo_k": num(k), "rank_a": "", "rank_b": "", "ot": int(res in (1, 2)),
            "mark": "", "note": "", "source_row": g["game_id"], "string_source": "engine",
            "raw_winner": "", "raw_loser": "",
        })
        per_team[h].append((res, g["game_type"], True, v))
        per_team[v].append((3 - res, g["game_type"], False, h))

    # 1. Every displayed record and game log equals the frozen DATA's.
    for dex, t in teams.items():
        gl = per_team[dex]
        mine = {
            "overall": record([r for r, *_ in gl]),
            "regional": record([r for r, ty, *_ in gl if ty == "R"]),
            "league": record([r for r, ty, *_ in gl if ty == "L"]),
            "cup": record([r for r, ty, *_ in gl if ty == "S"]),
            "playoff_finals": record([r for r, ty, *_ in gl if ty in ("P", "F")]),
            "home": record([r for r, _, home, _ in gl if home]),
            "away": record([r for r, _, home, _ in gl if not home]),
        }
        for k, v in mine.items():
            if t[k] != v:
                errors.append(f"{t['name']}: {k} {v} from the games, {t[k]} frozen")
        log = [[opp, ty, r] for r, ty, _, opp in gl]
        if log != t["log"]:
            errors.append(f"{t['name']}: game log differs from the frozen record")
        if abs(elo[dex] - t["elo_raw"]) > 0.5:
            errors.append(f"{t['name']}: last game Elo {elo[dex]}, frozen final {t['elo_raw']}")
    if len(rows) != 3525:
        errors.append(f"{len(rows)} games, the frozen record has 3525")
    for label, vals in (("opening", s8.values()), ("final", elo.values())):
        if abs(sum(vals) - 288000) > 0.5:
            errors.append(f"{label} Elo totals {sum(vals)}, not 288000")
    if set(s8) != set(teams):
        errors.append("S8's final Elo and S9's teams differ")

    # 2. The Hall of Fame's S9 world title follows from the final's games.
    finals = [r for r in rows if r["event"].startswith("WC-F-")]
    wins = Counter(r["team_a"] if r["winner"] == "A" else r["team_b"] for r in finals)
    champ = teams[wins.most_common(1)[0][0]]["name"] if wins else None
    world = [x["name"] for x in hof["plaques"]["world"]["seasons"]["9"]["teams"]]
    if champ != world[0]:
        errors.append(f"world champion {champ} from the games, Hall of Fame {world[0]}")

    if errors:
        print(f"REFUSING to write S9: {len(errors)} check(s) failed")
        for e in errors[:40]:
            print("  " + e)
        sys.exit(1)

    os.makedirs(OUT, exist_ok=True)
    eh.write_csv(os.path.join(OUT, "games.csv"), eh.GAME_FIELDS, rows)
    eh.write_csv(os.path.join(OUT, "walkovers.csv"), ["round", "event", "team", "game_type", "result"],
                 [{"round": w["round"], "event": ev_of_round[w["round"]]["code"], "team": w["team_id"],
                   "game_type": w["game_type"], "result": w["result"]} for w in walkovers])
    team_rows = []
    for dex in sorted(teams):
        t, inp = teams[dex], inputs[str(dex)]
        team_rows.append({
            "team": dex, "season_dex": dex, "name": t["name"], "region": t["region"],
            "division": t["division"], "tier": "", "primary_type": inp["primary_type"],
            "basclm": inp["basclm"], "opening_rank": "", "opening_elo": eh._num(s8[dex]),
            "final_rank": t["rank"], "final_ovr": t["ovr"], "final_elo": eh._num(t["elo_raw"]),
            "accolades": "; ".join(inp["accolades_exported"]) if isinstance(inp["accolades_exported"], list)
            else (inp["accolades_exported"] or ""),
        })
    eh.write_csv(os.path.join(OUT, "teams.csv"),
                 ["team", "season_dex", "name", "region", "division", "tier", "primary_type", "basclm",
                  "opening_rank", "opening_elo", "final_rank", "final_ovr", "final_elo", "accolades"],
                 team_rows)
    shas = {p: hashlib.sha256(open(os.path.join(ROOT, p), "rb").read()).hexdigest()
            for p in ("archive/s9_final.json", "archive/s9_inputs.json")}
    with open(os.path.join(OUT, "events.json"), "w") as f:
        json.dump({
            "season": 9,
            "source": {"games": "Season 9 audit rebuild (Baccer Game S9 Stats.xlsm + results/s9/)",
                       "checked_against": shas},
            "games": len(rows), "walkovers": len(walkovers),
            "titles": {"world": world[:2]},
            "events": [{"round": e["round"], "code": e["code"], "title": e["title"],
                        "games": e["games"], "empty": e["games"] == 0} for e in events],
        }, f, indent=1)
        f.write("\n")

    # The cross-season identity map: S9's dex IS the canonical id.
    tpath = os.path.join(ROOT, "history", "teams.json")
    ident = json.load(open(tpath))
    for dex, t in teams.items():
        entry = ident.setdefault(str(dex), {"s9_name": t["name"], "seasons": {}})
        entry["seasons"]["9"] = {"dex": dex, "name": t["name"], "region": t["region"],
                                 "division": t["division"]}
        entry["seasons"] = dict(sorted(entry["seasons"].items(), key=lambda kv: int(kv[0])))
    ident = {k: ident[k] for k in sorted(ident, key=int)}
    with open(tpath, "w") as f:
        json.dump(ident, f, indent=1)
        f.write("\n")
    print(f"S9: {len(rows)} games, {len(walkovers)} walkovers, {len(events)} events "
          f"({sum(e['games'] == 0 for e in events)} empty), {len(teams)} teams -> history/s9/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
