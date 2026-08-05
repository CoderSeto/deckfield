"""
Regenerates the round/results-dependent data embedded in
deckfield_dashboard.html, in place, from the current deckfield.db.

Run this every time new results are added via `deckfield add-results` --
per explicit instruction, the dashboard should always be regenerated when
new results come in, not left stale until someone remembers to do it by
hand.

Covers: DATA (Rankings tab, which Standings derives from client-side),
TEAMS_EXPORT_TSV (Team Roster paste box), NEXT_MATCHDAY_DATA, CALENDAR_DATA,
and RANK_ELO_HISTORY. Each reconstruction was validated by recomputing at
round 11 (the last state before this script existed) and confirming a
byte-identical match against the dashboard's already-published DATA/
CALENDAR_DATA at that round -- see the 2026-08-05 dashboard section of
CLAUDE.md for that validation and for what this script does NOT cover yet
(STRENGTH_DATA and PA Cup's real-results display -- both stale/absent,
not silently wrong, until someone reverse-engineers those formulas with
the same confidence).

Usage: python3 regenerate_dashboard.py [dashboard_path]
"""
import json
import re
import sys

from deckfield_ratings import (
    get_connection, taper_n, export_teams_for_deckfield,
    export_matchday_batches, rank_elo_history,
)

SEASON = 9
DASHBOARD_PATH = sys.argv[1] if len(sys.argv) > 1 else "deckfield_dashboard.html"


def _records_for(conn, team_id, latest_round):
    """(record dict) for one team -- W-L strings per game-type bucket, plus
    the chronological [opponent_dex, game_type, result_a] log DATA.teams
    carries. Mirrors export_teams_for_deckfield's records_for(), extended
    to also return playoff_finals/regional_w/league_w/log since DATA (the
    Rankings tab) needs those and the Team Roster export doesn't."""
    games = conn.execute("""
        SELECT round, game_type, result_a, team_a, team_b FROM games
        WHERE season=? AND round<=? AND (team_a=? OR team_b=?) ORDER BY round
    """, (SEASON, latest_round, team_id, team_id)).fetchall()
    walkovers = conn.execute("""
        SELECT round, game_type, result FROM walkovers WHERE season=? AND round<=? AND team_id=? ORDER BY round
    """, (SEASON, latest_round, team_id)).fetchall()
    buckets = {"R": [0, 0], "L": [0, 0], "S": [0, 0], "PF": [0, 0]}
    overall = [0, 0]
    log = []
    for g in games:
        result = g["result_a"] if g["team_a"] == team_id else 3 - g["result_a"]
        won = result in (2, 3)
        key = "PF" if g["game_type"] in ("P", "F") else g["game_type"]
        buckets[key][0 if won else 1] += 1
        overall[0 if won else 1] += 1
        opp = g["team_b"] if g["team_a"] == team_id else g["team_a"]
        log.append([opp, g["game_type"], result])
    for w in walkovers:
        won = w["result"] in (2, 3)
        key = "PF" if w["game_type"] in ("P", "F") else w["game_type"]
        buckets[key][0 if won else 1] += 1
        overall[0 if won else 1] += 1
    fmt = lambda p: f"{p[0]}-{p[1]}"
    return {
        "overall": fmt(overall), "regional": fmt(buckets["R"]), "league": fmt(buckets["L"]),
        "cup": fmt(buckets["S"]), "playoff_finals": fmt(buckets["PF"]),
        "regional_w": buckets["R"][0], "league_w": buckets["L"][0], "log": log,
    }


def _dscr_avg(conn, team_id, latest_round, game_type):
    """Average raw DMAX score (games.dscr_a/dscr_b) across a team's games of
    one game_type -- DATA's dscr_regional_avg/dscr_league_avg. Confirmed
    exact against Canalave City's real numbers (85.74 regional, 183.75
    league) before trusting this for the full roster."""
    rows = conn.execute("""
        SELECT dscr_a, dscr_b, team_a FROM games
        WHERE season=? AND round<=? AND game_type=? AND (team_a=? OR team_b=?)
    """, (SEASON, latest_round, game_type, team_id, team_id)).fetchall()
    if not rows:
        return 0.0
    vals = [r["dscr_a"] if r["team_a"] == team_id else r["dscr_b"] for r in rows]
    return round(sum(vals) / len(vals), 2)


def _raw_elo(conn, team_id, latest_round):
    row = conn.execute("""
        SELECT elo_a_after, elo_b_after, team_a FROM games
        WHERE season=? AND round<=? AND (team_a=? OR team_b=?)
        ORDER BY round DESC LIMIT 1
    """, (SEASON, latest_round, team_id, team_id)).fetchone()
    if row is None:
        return None
    return row["elo_a_after"] if row["team_a"] == team_id else row["elo_b_after"]


def build_data():
    """DATA -- the Rankings tab's full per-team table. Standings derives
    from this client-side, so this covers both tabs."""
    conn = get_connection()
    latest_round = conn.execute(
        "SELECT MAX(round) m FROM team_round_ratings WHERE season=?", (SEASON,)
    ).fetchone()["m"]
    rows = conn.execute("""
        SELECT r.*, t.name, t.region, ts.league_division
        FROM team_round_ratings r
        JOIN teams t ON t.team_id = r.team_id
        JOIN team_seasons ts ON ts.team_id = r.team_id AND ts.season = r.season
        WHERE r.season = ? AND r.round = ?
        ORDER BY r.ovr DESC
    """, (SEASON, latest_round)).fetchall()

    teams_out = []
    for rank, r in enumerate(rows, start=1):
        rec = _records_for(conn, r["team_id"], latest_round)
        teams_out.append({
            "dex": r["team_id"], "name": r["name"], "region": r["region"], "division": r["league_division"],
            "overall": rec["overall"], "regional": rec["regional"], "league": rec["league"], "cup": rec["cup"],
            "playoff_finals": rec["playoff_finals"], "regional_w": rec["regional_w"], "league_w": rec["league_w"],
            "rp": int(r["rp"]), "lp": int(r["lp"]),
            "dscr_regional_avg": _dscr_avg(conn, r["team_id"], latest_round, "R"),
            "dscr_league_avg": _dscr_avg(conn, r["team_id"], latest_round, "L"),
            "tot": round(r["tot"]), "elo_raw": round(_raw_elo(conn, r["team_id"], latest_round) or 0),
            "ovr": round(r["ovr"], 2), "rw": round(r["rw"], 2), "lw": round(r["lw"], 2),
            "pf": round(r["pf_norm"], 2), "pa": round(r["pa_norm"], 2), "pdg": round(r["pdg"], 2),
            "sos": round(r["sos"], 2), "sov": round(r["sov"], 2), "dscr": round(r["dscr_comp"], 2),
            "eye": round(r["eye"], 2), "elo_comp": round(r["elo_comp"], 2), "cups": round(r["cups"], 2),
            "ex": round(r["ex_norm"], 2), "fatigue": round(r["fatigue_after"], 1) if r["fatigue_after"] is not None else None,
            "rlstr": round(r["rlstr_own"], 4), "clim": round(r["climate"], 2), "grade": r["grade"],
            "log": rec["log"], "rank": rank,
        })
    conn.close()
    return {"season": SEASON, "round": latest_round, "ex_taper_n": taper_n(latest_round), "teams": teams_out}


def build_next_matchday():
    info, batches = export_matchday_batches(SEASON)
    if info is None:
        return None
    event_label = batches[0]["label"] if len(batches) == 1 else "RDS/PA combined"
    return {
        "week": info["week"], "day": info["day"], "event_label": event_label,
        "abs_round": info["abs_round"],
        "batches": [
            {"label": b["label"], "settings_tsv": b["settings_tsv"],
             "matchups_tsv": b["matchups_tsv"], "games": b["games"]}
            for b in batches
        ],
    }


def build_calendar():
    from deckfield_ratings import WEEKLY_SCHEDULE, abs_round_for_event, next_matchday
    conn = get_connection()
    next_info = next_matchday(SEASON)
    next_event = next_info["event"] if next_info else None

    def label_for(slot, day):
        kind = slot[0]
        if kind in ("R", "L"):
            return f"{kind}{slot[1]}"
        if kind == "RT":
            return f"RT{slot[1]}"
        _, bracket, cup_round = slot
        if cup_round is not None:
            return f"{kind} {bracket} {cup_round}"
        if bracket == "Final" and kind == "PA":
            n = {"Tue": 1, "Thu": 2, "Weekend": 3}[day]
            return f"PA Final {n}"
        return f"{kind} {bracket}"

    def played(abs_round):
        return conn.execute(
            "SELECT COUNT(*) c FROM games WHERE season=? AND round=?", (SEASON, abs_round)
        ).fetchone()["c"] > 0

    calendar = []
    for week in WEEKLY_SCHEDULE:
        entry = {"week": week["week"]}
        for day in ("Tue", "Thu", "Weekend"):
            slot = week[day]
            if slot is None:
                entry[day] = None
                continue
            abs_round = abs_round_for_event(slot)
            entry[day] = {
                "label": label_for(slot, day), "played": played(abs_round),
                "abs_round": abs_round, "is_next": (slot == next_event),
            }
        calendar.append(entry)
    conn.close()
    return calendar


def build_rank_elo_history():
    h = rank_elo_history(SEASON)
    return {
        "rank_checkpoints": [c["label"] for c in h["rank_checkpoints"]],
        "elo_checkpoints": [c["label"] for c in h["elo_checkpoints"]],
        "teams": [
            {"dex": t["team_id"], "name": t["name"],
             "rank_history": t["rank_history"], "elo_history": t["elo_history"]}
            for t in h["teams"]
        ],
    }


def _js_string_literal(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\t', '\\t').replace('\n', '\\n') + '"'


def _replace_const(content, var_name, value, is_array=False):
    open_char, close_char = (r'\[', r'\]') if is_array else (r'\{', r'\}')
    pattern = re.compile(rf'const {var_name} = {open_char}.*?{close_char};\n')
    new_content, n = pattern.subn(lambda m: f'const {var_name} = {json.dumps(value)};\n', content, count=1)
    if n != 1:
        raise RuntimeError(f"expected exactly one `const {var_name} = ...;` block, found {n}")
    return new_content


def main():
    with open(DASHBOARD_PATH) as f:
        content = f.read()

    content = _replace_const(content, "DATA", build_data())

    next_md = build_next_matchday()
    if next_md is not None:
        content = _replace_const(content, "NEXT_MATCHDAY_DATA", next_md)

    content = _replace_const(content, "CALENDAR_DATA", build_calendar(), is_array=True)
    content = _replace_const(content, "RANK_ELO_HISTORY", build_rank_elo_history())

    teams_out, tsv = export_teams_for_deckfield(SEASON)
    pattern = re.compile(r'const TEAMS_EXPORT_TSV = "(?:[^"\\]|\\.)*";\n')
    content, n = pattern.subn(lambda m: f'const TEAMS_EXPORT_TSV = {_js_string_literal(tsv)};\n', content, count=1)
    if n != 1:
        raise RuntimeError(f"expected exactly one `const TEAMS_EXPORT_TSV = ...;` block, found {n}")

    with open(DASHBOARD_PATH, "w") as f:
        f.write(content)
    print(f"Regenerated DATA, NEXT_MATCHDAY_DATA, CALENDAR_DATA, RANK_ELO_HISTORY, TEAMS_EXPORT_TSV in {DASHBOARD_PATH}")
    print("NOTE: STRENGTH_DATA (RL Strength tab) and PA Cup real-results are NOT covered -- see CLAUDE.md.")


if __name__ == "__main__":
    main()
