#!/usr/bin/env python3
"""
Freeze the per-team inputs the NEXT season needs from a finished one.

    python3 freeze_season_inputs.py 9

Companion to freeze_season.py. That one freezes what the dashboard SHOWED;
some of what the next season needs was never on the dashboard in usable form
(raw 0-10 DSCR, Primary Type, base climate, stored accolades, game-level DSCR
for the capped tiebreak, and the final standings ORDER, which the page only
works out in the browser). This writes them to archive/s<N>_inputs.json so the
next season never has to read the workbook or recompute the finished one.

Reads the database, which must be the faithful rebuild of season N
(see CLAUDE.md's runbook), and refuses to write unless every value that
overlaps the frozen record (archive/s<N>_final.json and the frozen
dashboard's roster export) agrees with it. Refuses to overwrite, and appends
its checksum to archive/SHA256SUMS like freeze_season.py.
"""
import datetime
import hashlib
import json
import os
import re
import sys

import deckfield_ratings as db

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(HERE, "archive")
SUMS_PATH = os.path.join(ARCHIVE_DIR, "SHA256SUMS")

# Per-game DSCR cap for the standings / RDS-seeding tiebreak (SEASON10_PLAN
# B9a): 400 = 20^2, so no single game counts for more than 200 on the
# displayed sqrt(avg) x 10 scale. OVR's own DSCR is untouched by this.
TIEBREAK_DSCR_CAP = 400.0


def _dscr_avgs(conn, season, through, game_type):
    """{team_id: (uncapped average, capped average)} of each team's own
    per-game DSCR over one game type -- the same games the dashboard's
    dscr_regional_avg / dscr_league_avg average."""
    per = {}
    for r in conn.execute(
        "SELECT team_a, team_b, dscr_a, dscr_b FROM games "
        "WHERE season=? AND round<=? AND game_type=?", (season, through, game_type)):
        for tid, d in ((r["team_a"], r["dscr_a"]), (r["team_b"], r["dscr_b"])):
            if d is not None:
                per.setdefault(tid, []).append(d)
    return {t: (sum(v) / len(v), sum(min(x, TIEBREAK_DSCR_CAP) for x in v) / len(v))
            for t, v in per.items()}


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("usage: python3 freeze_season_inputs.py <season>")
    season = int(sys.argv[1])
    out_path = os.path.join(ARCHIVE_DIR, f"s{season}_inputs.json")
    if os.path.exists(out_path):
        sys.exit(f"refusing to overwrite {os.path.relpath(out_path, HERE)} -- a frozen season stays frozen")

    final = json.load(open(os.path.join(ARCHIVE_DIR, f"s{season}_final.json")))
    shown = {t["dex"]: t for t in final["data"]["DATA"]["teams"]}
    through = final["meta"]["through_round"]

    conn = db.get_connection()
    db_last = conn.execute("SELECT MAX(round) m FROM games WHERE season=?", (season,)).fetchone()["m"]
    if db_last != through:
        sys.exit(f"database is at round {db_last}, the frozen record at {through} -- rebuild first")

    frozen_page = open(os.path.join(HERE, final["meta"]["frozen_dashboard"])).read()
    tsv = json.loads(re.search(r'^const TEAMS_EXPORT_TSV = (".*");$', frozen_page, re.M).group(1))
    head, *rows = tsv.split("\n")
    roster = {int(r.split("\t")[1]): dict(zip(head.split("\t"), r.split("\t"))) for r in rows}

    ratings = {r["team_id"]: r for r in conn.execute(
        "SELECT * FROM team_round_ratings WHERE season=? AND round=?", (season, through))}
    seasons = {r["team_id"]: r for r in conn.execute(
        "SELECT * FROM team_seasons WHERE season=?", (season,))}
    teams_db = {r["team_id"]: r for r in conn.execute("SELECT * FROM teams")}
    reg_d = _dscr_avgs(conn, season, through, "R")
    lg_d = _dscr_avgs(conn, season, through, "L")
    exported = {t["dex"]: t for t in db.export_teams_for_deckfield(season)[0]}

    problems = []
    def agree(label, a, b, tol=0.0):
        same = abs(float(a) - float(b)) <= tol if tol else a == b
        if not same:
            problems.append(f"{label}: database {a!r} vs frozen {b!r}")

    teams = {}
    for dex, s in sorted(shown.items()):
        r, ts, tm, ex = ratings[dex], seasons[dex], teams_db[dex], exported[dex]
        ro = roster[dex]
        # Every overlap with the frozen record must agree before anything is written.
        agree(f"{s['name']} name", tm["name"], s["name"])
        agree(f"{s['name']} OVR", round(r["ovr"], 2), s["ovr"])
        agree(f"{s['name']} PF", round(r["pf_norm"], 2), s["pf"])
        agree(f"{s['name']} PA", round(r["pa_norm"], 2), s["pa"])
        agree(f"{s['name']} fatigue", round(r["fatigue_after"], 1), s["fatigue"])
        agree(f"{s['name']} TOT", round(r["tot"]), s["tot"])
        # The roster carries DSCR to 2dp; compare there (re-rounding 7.55 to 1dp in
        # binary floating point gives 7.5 while the true 7.5538 gives 7.6).
        agree(f"{s['name']} raw DSCR", round(r["d_sqrt_raw"], 2), float(ro["DSCR"]))
        agree(f"{s['name']} Primary Type", str(tm["primary_type"]), ro["Primary Type"])
        agree(f"{s['name']} accolades", ex["accolades"], ro["Accolades"])
        agree(f"{s['name']} climate", round((ts["basclm"] / 100) * (r["ovr"] + 50), 2), s["clim"], 0.011)
        agree(f"{s['name']} regional DSCR avg", round(reg_d[dex][0], 2), s["dscr_regional_avg"], 0.006)
        agree(f"{s['name']} league DSCR avg", round(lg_d[dex][0], 2), s["dscr_league_avg"], 0.006)
        teams[str(dex)] = {
            "name": s["name"],
            "region": s["region"],
            "division": s["division"],
            "primary_type": tm["primary_type"],
            "basclm": ts["basclm"],
            "ex_seed": ts["ex"],
            "final_ovr": r["ovr"],
            "final_pf_norm": r["pf_norm"],
            "final_pa_norm": r["pa_norm"],
            "final_d_sqrt_raw": r["d_sqrt_raw"],
            "final_fatigue": r["fatigue_after"],
            "final_elo": s["elo_raw"],
            "final_tot": r["tot"],
            "final_rank": s["rank"],
            "accolades_stored": tm["accolades"],
            "accolades_exported": ex["accolades"],
            "dscr_regional_avg": reg_d[dex][0],
            "dscr_league_avg": lg_d[dex][0],
            "dscr_regional_avg_cap400": reg_d[dex][1],
            "dscr_league_avg_cap400": lg_d[dex][1],
        }

    if problems:
        print(f"REFUSING to write: {len(problems)} value(s) disagree with the frozen record:")
        for p in problems[:40]:
            print("  -", p)
        sys.exit(1)

    regions = [r["region"] for r in conn.execute("SELECT DISTINCT region FROM teams ORDER BY region")]
    payload = {
        "meta": {
            "season": season,
            "through_round": through,
            "frozen_on": datetime.date.today().isoformat(),
            "source": "faithful rebuild of the season's database, cross-checked against "
                      f"archive/s{season}_final.json and {final['meta']['frozen_dashboard']}",
            "tiebreak_dscr_cap": TIEBREAK_DSCR_CAP,
            "notes": {
                "accolades_stored": "workbook history (seasons before this one), as migrated",
                "accolades_exported": "what the end-of-season roster carried: stored history plus "
                                      "this season's cup, RT and Division One titles (no World "
                                      "Championship titles -- the export never awarded them)",
                "final_d_sqrt_raw": "raw 0-10 DSCR (the carryover seed), not the 0-100 OVR component",
                "dscr_*_cap400": "average with each game capped at 400, for the tiebreak only",
                "regional_standings / division_standings": "final order 1-16 by the season's own "
                    "standings rule (W-L, head-to-head, then uncapped DSCR)",
            },
        },
        "teams": teams,
        "regional_standings": {g: [db.regional_standings_seeds(season, g)[i] for i in range(1, 17)]
                               for g in regions},
        "division_standings": {str(d): [db.division_standings_seeds(season, d)[i] for i in range(1, 17)]
                               for d in range(1, 11)},
    }

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(out_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(SUMS_PATH, "a") as f:
        f.write(f"{digest}  {os.path.relpath(out_path, HERE)}\n")
    print(f"Froze {len(teams)} teams' season-{season} inputs to {os.path.relpath(out_path, HERE)}; "
          f"checksum appended to archive/SHA256SUMS")


if __name__ == "__main__":
    main()
