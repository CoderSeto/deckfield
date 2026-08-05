"""
DECKFIELD ratings engine.

Implements deckfield_ranking_spec.md end to end: schema, game logging, and
the RW / LW / OVR / Grade / Climate / Playoff Score calculations, run in
strict round order (required because SOS depends on opponents' OVR from
the previous round). Also includes Regional/League Strength (RLStr, a
z-score + tanh curve over each region/division), full S8 season carryover,
fatigue accrual, and bracket generation for the RDS and PA Cups.

Known open items:
  - Fatigue and Cup bracket advancement beyond what's been validated against
    real Season 9 results depend on host_region data / real winners that
    don't exist yet for future rounds.
"""

import sqlite3
import json
import math
import random
import statistics
from pathlib import Path
from math import sqrt
from collections import defaultdict

DB_PATH = Path(__file__).parent / "deckfield.db"
BASELINE_OVR = 50.0  # stand-in for an un-rated opponent, pending S8 carryover
STARTING_ELO = 1800.0  # confirmed; matters for a team's very first game ever, and if new teams are ever added
STARTING_TOT = 20.0  # every team starts the season at TOT=20, as a floor against going negative
GAME_TYPE_POINT_MULTIPLIER = {"R": 1, "L": 1, "S": 2, "P": 8, "F": 12}

# ---------------------------------------------------------------- schema --

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Builds the schema from scratch every time -- deliberately DROPs all
    tables first. Without this, re-running the migration on an existing
    database silently duplicates every row (CREATE TABLE IF NOT EXISTS
    doesn't touch existing data), which is exactly the kind of footgun a
    CLI meant to be safely re-run can't have."""
    conn = get_connection()
    conn.executescript("""
    DROP TABLE IF EXISTS team_round_ratings;
    DROP TABLE IF EXISTS games;
    DROP TABLE IF EXISTS walkovers;
    DROP TABLE IF EXISTS fatigue_deltas;
    DROP TABLE IF EXISTS season_carryover_seeds;
    DROP TABLE IF EXISTS region_distances;
    DROP TABLE IF EXISTS team_seasons;
    DROP TABLE IF EXISTS teams;
    """)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS teams (
        team_id INTEGER PRIMARY KEY,        -- DEX
        name TEXT NOT NULL UNIQUE,
        region TEXT NOT NULL,
        primary_type TEXT,
        accolades TEXT
    );

    CREATE TABLE IF NOT EXISTS team_seasons (
        team_id INTEGER NOT NULL REFERENCES teams(team_id),
        season INTEGER NOT NULL,
        league_division INTEGER NOT NULL,   -- 1-10
        basclm REAL NOT NULL,               -- base climate, fixed/season
        ex REAL NOT NULL,                   -- EX seed, fixed/season
        rlstr REAL NOT NULL DEFAULT 1,      -- placeholder, see module docstring
        starting_fatigue REAL,              -- season-opening fatigue baseline (R1 value)
        PRIMARY KEY (team_id, season)
    );

    CREATE TABLE IF NOT EXISTS games (
        game_id INTEGER PRIMARY KEY AUTOINCREMENT,
        season INTEGER NOT NULL,
        round INTEGER NOT NULL,             -- matchday; one game per team per round
        game_type TEXT NOT NULL CHECK(game_type IN ('R','L','S','P','F')),
        team_a INTEGER NOT NULL REFERENCES teams(team_id),
        team_b INTEGER NOT NULL REFERENCES teams(team_id),
        pf_a INTEGER NOT NULL,
        pa_a INTEGER NOT NULL,              -- = pf_b
        result_a INTEGER NOT NULL CHECK(result_a IN (0,1,2,3)),
        raw_goal_score_a REAL,
        raw_goal_score_b REAL,
        spread_a REAL,                      -- positive = team_a favored
        interest_score REAL,                -- game-level (GI)
        dscr_a REAL,                        -- per-game DMAX Score, team_a
        dscr_b REAL,
        ex_bonus_a REAL DEFAULT 0,          -- manual per-round entry (B)
        ex_bonus_b REAL DEFAULT 0,
        elo_a_after REAL,
        elo_b_after REAL,
        host_region TEXT,                   -- which region physically hosted the game (fatigue input)
        cup_name TEXT,                      -- Ribbon/Dream/Star/PA -- which cup this game belongs to, if any
        cup_bracket TEXT,                   -- Draw/Process
        cup_round INTEGER                   -- 1st/2nd/... round WITHIN the cup (not the absolute season round)
    );

    CREATE TABLE IF NOT EXISTS team_round_ratings (
        team_id INTEGER NOT NULL,
        season INTEGER NOT NULL,
        round INTEGER NOT NULL,
        rp REAL, lp REAL, sp REAL, p REAL, f REAL, b REAL, tot REAL,
        rw REAL, lw REAL,
        pf_norm REAL, pa_norm REAL, pdg REAL, sos REAL, sov REAL,
        dscr_comp REAL, eye REAL, elo_comp REAL, cups REAL, ex_norm REAL,
        ovr REAL, grade TEXT, climate REAL, playoff_score REAL,
        pf_raw REAL, pa_raw REAL, d_sqrt_raw REAL, rlstr_own REAL, fatigue_after REAL,
        PRIMARY KEY (team_id, season, round)
    );

    CREATE TABLE IF NOT EXISTS season_carryover_seeds (
        team_id INTEGER NOT NULL,
        season INTEGER NOT NULL,            -- the new season these seeds apply to
        ovr_seed REAL, fatigue_seed REAL, dscr_seed REAL, pf_seed REAL, pa_seed REAL,
        PRIMARY KEY (team_id, season)
    );

    CREATE TABLE IF NOT EXISTS walkovers (
        walkover_id INTEGER PRIMARY KEY AUTOINCREMENT,
        season INTEGER NOT NULL,
        round INTEGER NOT NULL,
        game_type TEXT NOT NULL CHECK(game_type IN ('R','L','S','P','F')),
        team_id INTEGER NOT NULL REFERENCES teams(team_id),
        result INTEGER NOT NULL CHECK(result IN (0,1,2,3))
        -- Awards points and a win/loss same as a real game, but has no
        -- opponent or stats and does NOT count toward games_played
        -- (matches the sheet's own GAMES formula, which explicitly
        -- subtracts these back out).
    );

    CREATE TABLE IF NOT EXISTS fatigue_deltas (
        team_id INTEGER NOT NULL,
        season INTEGER NOT NULL,
        round INTEGER NOT NULL,
        delta REAL NOT NULL,
        -- this round's travel-fatigue contribution; cumulative fatigue for
        -- a round = team_seasons.starting_fatigue + sum of deltas through
        -- that round (round 1 itself has no delta -- it IS the starting value)
        PRIMARY KEY (team_id, season, round)
    );

    CREATE TABLE IF NOT EXISTS region_distances (
        region_a TEXT NOT NULL,
        region_b TEXT NOT NULL,
        distance INTEGER NOT NULL,
        PRIMARY KEY (region_a, region_b)
    );
    """)
    conn.commit()
    conn.close()


REGION_DISTANCE_MATRIX = {
    "Indigo":     {"Indigo":1,"Delta":2,"LilyValley":2,"Vertress":8,"Kalosite":8,"Lanakila":4,"Dynamax":8,"Phoenix":8,"Silver":2,"Terastal":8},
    "Delta":      {"Indigo":2,"Delta":1,"LilyValley":2,"Vertress":8,"Kalosite":8,"Lanakila":4,"Dynamax":8,"Phoenix":8,"Silver":2,"Terastal":8},
    "LilyValley": {"Indigo":2,"Delta":2,"LilyValley":1,"Vertress":8,"Kalosite":8,"Lanakila":4,"Dynamax":8,"Phoenix":8,"Silver":2,"Terastal":8},
    "Vertress":   {"Indigo":8,"Delta":8,"LilyValley":8,"Vertress":1,"Kalosite":4,"Lanakila":8,"Dynamax":4,"Phoenix":2,"Silver":8,"Terastal":4},
    "Kalosite":   {"Indigo":8,"Delta":8,"LilyValley":8,"Vertress":4,"Kalosite":1,"Lanakila":16,"Dynamax":2,"Phoenix":4,"Silver":8,"Terastal":2},
    "Lanakila":   {"Indigo":4,"Delta":4,"LilyValley":4,"Vertress":8,"Kalosite":16,"Lanakila":1,"Dynamax":16,"Phoenix":4,"Silver":4,"Terastal":16},
    "Dynamax":    {"Indigo":8,"Delta":8,"LilyValley":8,"Vertress":4,"Kalosite":2,"Lanakila":16,"Dynamax":1,"Phoenix":4,"Silver":8,"Terastal":2},
    "Phoenix":    {"Indigo":8,"Delta":8,"LilyValley":8,"Vertress":2,"Kalosite":4,"Lanakila":4,"Dynamax":4,"Phoenix":1,"Silver":8,"Terastal":4},
    "Silver":     {"Indigo":2,"Delta":2,"LilyValley":2,"Vertress":8,"Kalosite":8,"Lanakila":4,"Dynamax":8,"Phoenix":8,"Silver":1,"Terastal":8},
    "Terastal":   {"Indigo":8,"Delta":8,"LilyValley":8,"Vertress":4,"Kalosite":2,"Lanakila":16,"Dynamax":2,"Phoenix":4,"Silver":8,"Terastal":1},
}


def seed_region_distances():
    conn = get_connection()
    rows = [(a, b, d) for a, inner in REGION_DISTANCE_MATRIX.items() for b, d in inner.items()]
    conn.executemany(
        "INSERT OR REPLACE INTO region_distances (region_a, region_b, distance) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def fatigue(previous_region, current_region, skipped_matchdays=0):
    raw = REGION_DISTANCE_MATRIX[previous_region][current_region]
    return raw / (2 ** skipped_matchdays)


def add_fatigue_delta(team_id, season, round_num, delta):
    """Store this round's travel-fatigue contribution. Round 1 itself
    doesn't need one -- its value comes from team_seasons.starting_fatigue
    directly."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO fatigue_deltas (team_id, season, round, delta) VALUES (?,?,?,?)",
        (team_id, season, round_num, delta),
    )
    conn.commit()
    conn.close()


def cumulative_fatigue(team_id, season, through_round):
    """starting_fatigue + every delta up through `through_round`. This is
    what a team's fatigue actually is at that point in the season -- the
    raw fatigue() lookup only gives one round's contribution, not the
    running total."""
    conn = get_connection()
    starting = conn.execute(
        "SELECT starting_fatigue FROM team_seasons WHERE team_id = ? AND season = ?",
        (team_id, season),
    ).fetchone()
    deltas = conn.execute(
        "SELECT delta FROM fatigue_deltas WHERE team_id = ? AND season = ? AND round <= ?",
        (team_id, season, through_round),
    ).fetchall()
    conn.close()
    base = starting["starting_fatigue"] if starting and starting["starting_fatigue"] is not None else 0.0
    return base + sum(d["delta"] for d in deltas)


# --------------------------------------------------------------- helpers --

def normalize(value, all_values):
    """Standard min-max normalization to 0-100. Neutral 50 if the league
    has no spread at all (every team tied) to avoid a divide by zero."""
    lo, hi = min(all_values), max(all_values)
    if hi == lo:
        return 50.0
    return (value - lo) / (hi - lo) * 100


def week_for_round(round_num):
    """Weeks 1-3 are a single matchday each; weeks 4-30 are 3 matchdays each."""
    if round_num <= 3:
        return round_num
    return 4 + (round_num - 4) // 3


def taper_n(round_num):
    """N in OVR's N/14 taper term, keyed off week number (see week_for_round).
    Changed from the original 12-based taper: full weight (14) through week
    6, decreasing by 1/week starting week 7, reaching 1 at week 19, 0 from
    week 20 on -- mirrors the original 12-based structure's shape (full
    through week 6, 0 starting 12 weeks later at week 18) extended
    proportionally to the new max of 14 (0 starting 14 weeks after week 6,
    at week 20)."""
    week = week_for_round(round_num)
    if week <= 6:
        return 14
    if week >= 20:
        return 0
    return 14 - (week - 6)


def grade_for(ovr):
    sqrt_col = sqrt(max(ovr, 0) / 100)
    thresholds = [
        (15/30, "F"), (18/30, "F+"), (19/30, "EF"), (20/30, "E"),
        (21/30, "DE"), (22/30, "D"), (23/30, "CD"), (24/30, "C"),
        (25/30, "BC"), (26/30, "B"), (27/30, "AB"), (28/30, "A"),
        (29/30, "S"),
    ]
    for cutoff, label in thresholds:
        if sqrt_col < cutoff:
            return label
    return "SS"


# ------------------------------------------------------------ data entry --

def add_team(team_id, name, region, primary_type=None, accolades=None):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO teams (team_id, name, region, primary_type, accolades) "
        "VALUES (?,?,?,?,?)",
        (team_id, name, region, primary_type, accolades),
    )
    conn.commit()
    conn.close()


def set_team_season(team_id, season, league_division, basclm, ex, rlstr=1.0, starting_fatigue=None):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO team_seasons (team_id, season, league_division, basclm, ex, rlstr, starting_fatigue) "
        "VALUES (?,?,?,?,?,?,?)",
        (team_id, season, league_division, basclm, ex, rlstr, starting_fatigue),
    )
    conn.commit()
    conn.close()


def add_game(season, round_num, game_type, team_a, team_b, pf_a, pa_a, result_a,
             raw_goal_score_a=None, raw_goal_score_b=None, spread_a=None,
             interest_score=None, dscr_a=None, dscr_b=None,
             ex_bonus_a=0, ex_bonus_b=0, elo_a_after=None, elo_b_after=None,
             host_region=None, cup_name=None, cup_bracket=None, cup_round=None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO games (season, round, game_type, team_a, team_b, pf_a, pa_a, result_a,
                            raw_goal_score_a, raw_goal_score_b, spread_a, interest_score,
                            dscr_a, dscr_b, ex_bonus_a, ex_bonus_b, elo_a_after, elo_b_after,
                            host_region, cup_name, cup_bracket, cup_round)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (season, round_num, game_type, team_a, team_b, pf_a, pa_a, result_a,
          raw_goal_score_a, raw_goal_score_b, spread_a, interest_score,
          dscr_a, dscr_b, ex_bonus_a, ex_bonus_b, elo_a_after, elo_b_after,
          host_region, cup_name, cup_bracket, cup_round))
    conn.commit()
    conn.close()


CSV_GAME_FIELDS = [
    "round", "game_type", "team_a", "team_b", "result_a", "pf_a", "pa_a",
    "raw_goal_a", "raw_goal_b", "spread_a", "interest", "dscr_a", "dscr_b",
    "elo_a_after", "elo_b_after", "home",
]
# Optional trailing fields -- most games get these added later, not at entry time
CSV_GAME_OPTIONAL_FIELDS = ["ex_a", "ex_b"]


def recompute_all_fatigue_deltas(season):
    """
    Replace every team's fatigue_deltas with values freshly computed from
    their actual host_region history (via the fatigue() matrix lookup),
    superseding whatever was originally migrated from the sheet. Confirmed
    necessary: the sheet's own deltas didn't line up with its own Home/Away
    flags on cross-check, while this computation does by construction.
    Starting_fatigue (the season-opening seed) is untouched -- this only
    recomputes the round-by-round deltas.
    """
    conn = get_connection()
    team_ids = _all_team_ids(conn, season)
    teams_row = {r["team_id"]: r["region"] for r in conn.execute("SELECT team_id, region FROM teams").fetchall()}

    updated = 0
    for tid in team_ids:
        games = conn.execute("""
            SELECT round, host_region FROM games
            WHERE season = ? AND (team_a = ? OR team_b = ?) AND host_region IS NOT NULL
            ORDER BY round
        """, (season, tid, tid)).fetchall()

        prev_region = teams_row[tid]
        prev_round = 0
        for g in games:
            skipped = max(g["round"] - prev_round - 1, 0)
            delta = fatigue(prev_region, g["host_region"], skipped)
            conn.execute(
                "INSERT OR REPLACE INTO fatigue_deltas (team_id, season, round, delta) VALUES (?,?,?,?)",
                (tid, season, g["round"], delta),
            )
            updated += 1
            prev_region = g["host_region"]
            prev_round = g["round"]

    conn.commit()
    conn.close()
    return updated


def _record_fatigue_for_team(conn, team_id, season, round_num, this_host_region):
    """Compute and store this round's fatigue delta for one team, based on
    the region of their most recent prior game this season (or their own
    home region, if this is their first game of the season)."""
    prior = conn.execute("""
        SELECT round, host_region FROM games
        WHERE season = ? AND round < ? AND (team_a = ? OR team_b = ?) AND host_region IS NOT NULL
        ORDER BY round DESC LIMIT 1
    """, (season, round_num, team_id, team_id)).fetchone()
    if prior:
        previous_region = prior["host_region"]
        skipped = round_num - prior["round"] - 1
    else:
        previous_region = conn.execute(
            "SELECT region FROM teams WHERE team_id = ?", (team_id,)
        ).fetchone()["region"]
        skipped = 0
    delta = fatigue(previous_region, this_host_region, max(skipped, 0))
    conn.execute(
        "INSERT OR REPLACE INTO fatigue_deltas (team_id, season, round, delta) VALUES (?,?,?,?)",
        (team_id, season, round_num, delta),
    )
    return delta


def add_game_from_dict(season, values):
    """
    Shared core: add one full game from a dict of field values (already
    parsed -- from a pasted comma-separated row, a CSV file, or anywhere
    else). Both teams at once, no double entry needed. Computes and stores
    each team's fatigue delta for this round automatically, using `home`
    to resolve host_region.

    Required keys: round, game_type, team_a, team_b, result_a, pf_a, pa_a,
    raw_goal_a, raw_goal_b, spread_a, interest, dscr_a, dscr_b, elo_a_after,
    elo_b_after, home. Optional: ex_a, ex_b (default 0).

    - result_a: points team_a earned -- 3 win, 2 OT win, 1 OT loss, 0 loss
    - home: "A" or "B" -- which team hosted
    """
    home_side = str(values["home"]).strip().upper()
    if home_side not in ("A", "B"):
        raise ValueError(f"home must be 'A' or 'B', got {values['home']!r}")

    conn = get_connection()
    team_a_id, team_b_id = int(values["team_a"]), int(values["team_b"])
    round_num = int(values["round"])
    host_team_id = team_a_id if home_side == "A" else team_b_id
    host_row = conn.execute("SELECT region FROM teams WHERE team_id = ?", (host_team_id,)).fetchone()
    if host_row is None:
        conn.close()
        raise ValueError(f"No team on file with id {host_team_id}")
    host_region = host_row["region"]

    delta_a = _record_fatigue_for_team(conn, team_a_id, season, round_num, host_region)
    delta_b = _record_fatigue_for_team(conn, team_b_id, season, round_num, host_region)
    conn.commit()
    conn.close()

    add_game(
        season, round_num, values["game_type"], team_a_id, team_b_id,
        pf_a=int(values["pf_a"]), pa_a=int(values["pa_a"]), result_a=int(values["result_a"]),
        raw_goal_score_a=float(values["raw_goal_a"]), raw_goal_score_b=float(values["raw_goal_b"]),
        spread_a=float(values["spread_a"]), interest_score=float(values["interest"]),
        dscr_a=float(values["dscr_a"]), dscr_b=float(values["dscr_b"]),
        elo_a_after=float(values["elo_a_after"]), elo_b_after=float(values["elo_b_after"]),
        ex_bonus_a=float(values.get("ex_a") or 0), ex_bonus_b=float(values.get("ex_b") or 0),
        host_region=host_region,
        cup_name=values.get("cup_name") or None,
        cup_bracket=values.get("cup_bracket") or None,
        cup_round=int(values["cup_round"]) if values.get("cup_round") else None,
    )
    return {"round": round_num, "host_region": host_region, "fatigue_delta_a": delta_a, "fatigue_delta_b": delta_b}


def add_game_csv(season, row):
    """
    Add one full game from a single comma-separated row (e.g. pasted
    directly in chat) -- both teams at once, no double entry needed the
    way the old packed-string format required (one string per team, per
    game).

    Column order:
      round, game_type, team_a_dex, team_b_dex, result_a, pf_a, pa_a,
      raw_goal_a, raw_goal_b, spread_a, interest, dscr_a, dscr_b,
      elo_a_after, elo_b_after, home, [ex_a, ex_b]

    ex_a, ex_b: optional, default 0 -- can be added later once a winner's
    EX Bonus Score is known

    Example:
      "12,S,50,113,3,36,33,4.13,3.9,0.93,102,42.7,38.1,2183,2091,A"
    """
    parts = [p.strip() for p in row.split(",")]
    if len(parts) not in (len(CSV_GAME_FIELDS), len(CSV_GAME_FIELDS) + len(CSV_GAME_OPTIONAL_FIELDS)):
        raise ValueError(
            f"Expected {len(CSV_GAME_FIELDS)} or {len(CSV_GAME_FIELDS)+len(CSV_GAME_OPTIONAL_FIELDS)} "
            f"fields, got {len(parts)}: {row!r}"
        )
    fields = CSV_GAME_FIELDS + CSV_GAME_OPTIONAL_FIELDS
    values = dict(zip(fields, parts))
    result = add_game_from_dict(season, values)
    return {"host_region": result["host_region"], "fatigue_delta_a": result["fatigue_delta_a"],
            "fatigue_delta_b": result["fatigue_delta_b"]}


def recompute_from_round(season, start_round):
    """Recompute ratings for every round from start_round through the
    latest round with any game data, in order (required because SOS
    depends on the previous round's snapshot). Call this after adding new
    results so the ratings reflect them."""
    conn = get_connection()
    row = conn.execute("SELECT MAX(round) AS m FROM games WHERE season = ?", (season,)).fetchone()
    conn.close()
    if row is None or row["m"] is None:
        return []
    max_round = row["m"]
    touched = []
    for r in range(start_round, max_round + 1):
        compute_round_ratings(season, r)
        touched.append(r)
    return touched


# ------------------------------------------------------ per-team unpacking --

def _team_game_rows(conn, season, team_id, through_round):
    """Every game a team has played this season through a given round,
    unpacked to that team's own perspective regardless of which side of
    the games table (team_a/team_b) they were stored on."""
    rows = conn.execute("""
        SELECT round, game_type, team_a, team_b, pf_a, pa_a, result_a,
               raw_goal_score_a, raw_goal_score_b, spread_a, interest_score,
               dscr_a, dscr_b, ex_bonus_a, ex_bonus_b, elo_a_after, elo_b_after
        FROM games
        WHERE season = ? AND round <= ? AND (team_a = ? OR team_b = ?)
        ORDER BY round
    """, (season, through_round, team_id, team_id)).fetchall()

    out = []
    for r in rows:
        is_a = (r["team_a"] == team_id)
        result = r["result_a"] if is_a else (3 - r["result_a"])
        out.append({
            "round": r["round"],
            "game_type": r["game_type"],
            "opponent": r["team_b"] if is_a else r["team_a"],
            "pf": r["pf_a"] if is_a else r["pa_a"],
            "pa": r["pa_a"] if is_a else r["pf_a"],
            "result": result,                       # points earned, this team
            "is_win": result in (2, 3),
            "is_ot": result in (1, 2),
            "raw_goal_score": r["raw_goal_score_a"] if is_a else r["raw_goal_score_b"],
            "spread": r["spread_a"] if is_a else (-r["spread_a"] if r["spread_a"] is not None else None),
            "interest_score": r["interest_score"],
            "dscr": r["dscr_a"] if is_a else r["dscr_b"],
            "ex_bonus": r["ex_bonus_a"] if is_a else r["ex_bonus_b"],
            "elo_after": r["elo_a_after"] if is_a else r["elo_b_after"],
        })
    return out


def add_walkover(season, round_num, game_type, team_id, result):
    """A win/loss with no opponent and no stats -- awards points and a
    win/loss count, but never touches games_played or any per-game
    average (PF, PA, spread, DSCR, interest, Elo)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO walkovers (season, round, game_type, team_id, result) VALUES (?,?,?,?,?)",
        (season, round_num, game_type, team_id, result),
    )
    conn.commit()
    conn.close()


def _team_walkover_rows(conn, season, team_id, through_round):
    rows = conn.execute("""
        SELECT round, game_type, result FROM walkovers
        WHERE season = ? AND round <= ? AND team_id = ?
    """, (season, through_round, team_id)).fetchall()
    return [{"round": r["round"], "game_type": r["game_type"], "result": r["result"],
              "is_win": r["result"] in (2, 3)} for r in rows]


def _all_team_ids(conn, season):
    return [r["team_id"] for r in conn.execute(
        "SELECT DISTINCT team_id FROM team_seasons WHERE season = ?", (season,)
    ).fetchall()]


# --------------------------------------------------------- points & RW/LW --

def _points_buckets(games, walkovers=None):
    walkovers = walkovers or []

    def type_points(gtype):
        return (sum(g["result"] for g in games if g["game_type"] == gtype)
                + sum(w["result"] for w in walkovers if w["game_type"] == gtype))

    def type_wins(gtype):
        return (sum(1 for g in games if g["game_type"] == gtype and g["is_win"])
                + sum(1 for w in walkovers if w["game_type"] == gtype and w["is_win"]))

    regional_wins = type_wins("R")
    league_wins = type_wins("L")
    cup_wins = type_wins("S")

    # RP and LP each carry a bonus equal to the OTHER track's win count --
    # confirmed exact across all 160 real teams, not present on SP/P/F.
    rp = type_points("R") + league_wins
    lp = type_points("L") + regional_wins
    sp = type_points("S") * GAME_TYPE_POINT_MULTIPLIER["S"]
    p  = type_points("P") * GAME_TYPE_POINT_MULTIPLIER["P"]
    f  = type_points("F") * GAME_TYPE_POINT_MULTIPLIER["F"]
    b  = sum(g["ex_bonus"] or 0 for g in games)
    # Both teams now bank their own EX Bonus every game (win or lose), unlike the
    # historical S9 data where only the winner ever got a (positive-biased) bonus --
    # so a team on a bad enough streak of negative EX components could otherwise
    # drag TOT below zero. Floor it at 0 the same way STARTING_TOT was always meant
    # to act as a floor.
    tot = max(0.0, rp + lp + sp + p + f + b + STARTING_TOT)

    return {
        "rp": rp, "lp": lp, "sp": sp, "p": p, "f": f, "b": b, "tot": tot,
        # games_played counts are real games ONLY -- walkovers don't count,
        # matching the sheet's own GAMES formula, which explicitly excludes them
        "regional_games": sum(1 for g in games if g["game_type"] == "R"),
        "league_games": sum(1 for g in games if g["game_type"] == "L"),
        "regional_wins": regional_wins,
        "league_wins": league_wins,
        "cup_wins": cup_wins,
        "playoff_finals_wins": type_wins("P") + type_wins("F"),
    }


_UNDER3_TABLE = {
    (1, "1-0"): 200/3, (1, "0-1"): 100/3,
    (2, "2-0"): 500/6, (2, "1-1"): 50.0, (2, "0-2"): 100/6,
}


def _record_key(games, game_type):
    played = [g for g in games if g["game_type"] == game_type]
    wins = sum(1 for g in played if g["is_win"])
    losses = len(played) - wins
    return f"{wins}-{losses}"


def compute_rw(games, buckets):
    n = buckets["regional_games"]
    if n == 0:
        return 0.0
    if n < 3:
        return _UNDER3_TABLE[(n, _record_key(games, "R"))]
    denom = n * 3 + buckets["league_games"]  # league GAMES played, not wins
    return (buckets["rp"] / denom + buckets["sp"] / 1000
            + buckets["playoff_finals_wins"] * 0.005) * 100


def compute_lw(games, buckets, rw, league_division):
    n = buckets["league_games"]
    if n == 0:
        return rw * 0.75
    if n == 1:
        return (rw + _UNDER3_TABLE[(1, _record_key(games, "L"))]) / 2
    if n == 2:
        table_val = _UNDER3_TABLE[(2, _record_key(games, "L"))]
        return (rw + table_val) / 2 + (buckets["sp"] / 1000 + buckets["playoff_finals_wins"] * 0.005)
    denom = n * 3 + buckets["regional_games"]  # regional GAMES played, not wins
    lw_base = (buckets["lp"] / denom + buckets["sp"] / 1000
               + buckets["playoff_finals_wins"] * 0.005) * 100
    return lw_base * (1 + 0.05 * (11 - league_division))


# ------------------------------------------------------- full OVR pipeline --

def compute_round_ratings(season, round_num):
    """
    Compute and store every team's full rating line for one round.
    Must be called in round order -- SOS depends on the previous round's
    stored OVR, which this function reads back from team_round_ratings.
    """
    conn = get_connection()
    team_ids = _all_team_ids(conn, season)
    team_seasons = {r["team_id"]: r for r in conn.execute(
        "SELECT * FROM team_seasons WHERE season = ?", (season,)
    ).fetchall()}
    teams_row = {r["team_id"]: r for r in conn.execute("SELECT * FROM teams").fetchall()}
    prior_ovr = {r["team_id"]: r["ovr"] for r in conn.execute(
        "SELECT team_id, ovr FROM team_round_ratings WHERE season = ? AND round = ?",
        (season, round_num - 1),
    ).fetchall()}
    regional_strength, league_strength = compute_strength_scores(season, round_num)

    raw = {}  # team_id -> dict of un-normalized per-team values
    for tid in team_ids:
        games = _team_game_rows(conn, season, tid, round_num)
        walkovers = _team_walkover_rows(conn, season, tid, round_num)
        buckets = _points_buckets(games, walkovers)
        rw = compute_rw(games, buckets)
        lw = compute_lw(games, buckets, rw, team_seasons[tid]["league_division"])

        pf = sum(g["pf"] for g in games)
        pa = sum(g["pa"] for g in games)
        games_played = len(games)
        pd = pf - pa
        avg_pf = pf / games_played if games_played else 0.0
        avg_pa = pa / games_played if games_played else 0.0
        avg_pd = avg_pf - avg_pa

        spreads = [g["spread"] for g in games if g["spread"] is not None]
        avg_spread = sum(spreads) / len(spreads) if spreads else 0.0
        vic_avg = avg_pd - avg_spread

        raw_goals = [g["raw_goal_score"] for g in games if g["raw_goal_score"] is not None]
        avg_raw_goal_score = sum(raw_goals) / len(raw_goals) if raw_goals else 1.0

        dscrs = [g["dscr"] for g in games if g["dscr"] is not None]
        avg_dscr = sum(dscrs) / len(dscrs) if dscrs else 0.0
        d_sqrt = min(10.0, sqrt(max(avg_dscr, 0)))

        gis = [g["interest_score"] for g in games if g["interest_score"] is not None]
        avg_gi = sum(gis) / len(gis) if gis else 0.0

        played_elos = [g["elo_after"] for g in games if g["elo_after"] is not None]
        elo = played_elos[-1] if played_elos else STARTING_ELO

        opponent_ovrs = [prior_ovr.get(g["opponent"], BASELINE_OVR) for g in games]
        raw_sos = sum(opponent_ovrs) / len(opponent_ovrs) if opponent_ovrs else BASELINE_OVR
        region = teams_row[tid]["region"]
        division = team_seasons[tid]["league_division"]
        rlstr = (regional_strength[region] + league_strength[division]) / 2
        str_mod = raw_sos * rlstr

        raw[tid] = {
            "games": games, "buckets": buckets, "rw": rw, "lw": lw,
            "pf": pf, "pa": pa, "avg_pf": avg_pf, "avg_pa": avg_pa, "avg_pd": avg_pd,
            "games_played": games_played, "pd": pd, "avg_raw_goal_score": avg_raw_goal_score,
            "vic_avg": vic_avg, "d_sqrt": d_sqrt, "avg_gi": avg_gi, "elo": elo,
            "str_mod": str_mod, "ex": team_seasons[tid]["ex"], "rlstr": rlstr,
        }

    # ---- league-wide passes ----
    avg_pf_vals = [v["avg_pf"] for v in raw.values()]
    avg_pa_vals = [v["avg_pa"] for v in raw.values()]
    pd_vals = [v["pd"] for v in raw.values()]
    min_pd = min(pd_vals)
    avg_pd_vals = [v["avg_pd"] for v in raw.values()]
    min_avg_pd = min(avg_pd_vals)

    pd_per_gl = {}
    for tid, v in raw.items():
        pd_per_gl[tid] = ((v["avg_pd"] - min_avg_pd) / v["avg_raw_goal_score"]
                           if v["avg_raw_goal_score"] else 0.0)
    pd_per_gl_vals = list(pd_per_gl.values())

    vic_avg_vals = [v["vic_avg"] for v in raw.values()]
    d_sqrt_vals = [v["d_sqrt"] for v in raw.values()]
    elo_vals = [v["elo"] for v in raw.values()]
    str_mod_vals = [v["str_mod"] for v in raw.values()]
    ex_vals = [v["ex"] for v in raw.values()]
    tot_vals = [v["buckets"]["tot"] for v in raw.values()]

    # EYE's X depends on min_league(pd) too; recompute properly now that we have it
    ad_vals_pre = {}
    for tid, v in raw.items():
        x = (v["pd"] - min_pd) * (v["avg_gi"] / 100)
        y = sqrt(max(x, 0) / 100) * 100
        ad = ((x + y) / 2) * v["rlstr"]
        ad_vals_pre[tid] = (x, y, ad)
    ad_vals = [t[2] for t in ad_vals_pre.values()]

    n = taper_n(round_num)
    results = []
    for tid, v in raw.items():
        pf_range = (max(avg_pf_vals) - min(avg_pf_vals)) or 1
        pf_norm = ((v["avg_pf"] - min(avg_pf_vals)) / pf_range
                   + sqrt(max((v["avg_pf"] - min(avg_pf_vals)) / pf_range, 0))) / 2 * 100

        pa_range = (max(avg_pa_vals) - min(avg_pa_vals)) or 1
        pa_norm = (1 - (v["avg_pa"] - min(avg_pa_vals)) / pa_range) * 100

        pdg = pd_per_gl[tid] * 100 / (max(pd_per_gl_vals) or 1)
        sos = normalize(v["str_mod"], str_mod_vals)
        sov = normalize(v["vic_avg"], vic_avg_vals)

        d_sqrt_max = max(d_sqrt_vals) or 1
        d_sqrt_range = (max(d_sqrt_vals) - min(d_sqrt_vals)) or 1
        dscr_comp = ((v["d_sqrt"] / d_sqrt_max)
                     + (v["d_sqrt"] - min(d_sqrt_vals)) / d_sqrt_range) / 2 * 100

        x, y, ad = ad_vals_pre[tid]
        ad_max = max(ad_vals) or 1
        z = ad / ad_max
        eye = (z + sqrt(max(z, 0))) / 2 * 100

        elo_comp = normalize(v["elo"], elo_vals)
        cups = normalize(v["buckets"]["tot"], tot_vals)
        ex_norm = normalize(v["ex"], ex_vals)

        block_a = (pf_norm * .5 + pa_norm * .5) * 3
        block_b = (pf_norm * .35 + pa_norm * .35 + pdg * .3) * 2
        block_c = (sos * .4 + sov * .3 + dscr_comp * .3) * 3
        block_d = (eye * .35 + elo_comp * .3 + cups * .35) * 2
        block_e = ex_norm * (n / 14)
        ovr = (block_a + block_b + block_c + block_d + block_e) / (9 + n / 14)

        grade = grade_for(ovr)
        basclm = team_seasons[tid]["basclm"]
        climate = (basclm / 100) * (ovr + 50)
        inner_score = (elo_comp / 100 + dscr_comp * 3 + sos / 2.5) / 3
        playoff_score = (ovr / 4 + ovr / 5 + inner_score) / 3

        b = v["buckets"]
        fatigue_now = cumulative_fatigue(tid, season, round_num)
        results.append((
            tid, season, round_num,
            b["rp"], b["lp"], b["sp"], b["p"], b["f"], b["b"], b["tot"],
            v["rw"], v["lw"], pf_norm, pa_norm, pdg, sos, sov, dscr_comp, eye,
            elo_comp, cups, ex_norm, ovr, grade, climate, playoff_score,
            v["pf"], v["pa"], v["d_sqrt"], v["rlstr"], fatigue_now,
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO team_round_ratings
        (team_id, season, round, rp, lp, sp, p, f, b, tot, rw, lw,
         pf_norm, pa_norm, pdg, sos, sov, dscr_comp, eye, elo_comp, cups, ex_norm,
         ovr, grade, climate, playoff_score, pf_raw, pa_raw, d_sqrt_raw, rlstr_own, fatigue_after)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, results)
    conn.commit()
    conn.close()
    return results


# --------------------------------------------------- season carryover (S8) --

def compute_carryover_seeds(prev_ovr, prev_fatigue, prev_dscr, prev_pf, prev_pa):
    """
    Turn a team's final-round stats from the previous season into this
    season's opening seed values. OVR regresses 25% toward 50; Fatigue is
    divided by 5 and rounded up to the nearest integer; DSCR (raw 0-10
    D-Sqrt, NOT the normalized OVR component), PF, and PA carry over
    unchanged.
    """
    return {
        "ovr_seed": ((prev_ovr - 50) * 0.75) + 50,
        "fatigue_seed": math.ceil(prev_fatigue / 5),
        "dscr_seed": prev_dscr,
        "pf_seed": prev_pf,
        "pa_seed": prev_pa,
    }


def store_carryover_seeds(team_id, season, prev_ovr, prev_fatigue, prev_dscr, prev_pf, prev_pa):
    """Compute and persist a team's seeds for `season` from their final
    numbers in the season before it."""
    seeds = compute_carryover_seeds(prev_ovr, prev_fatigue, prev_dscr, prev_pf, prev_pa)
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO season_carryover_seeds
        (team_id, season, ovr_seed, fatigue_seed, dscr_seed, pf_seed, pa_seed)
        VALUES (?,?,?,?,?,?,?)
    """, (team_id, season, seeds["ovr_seed"], seeds["fatigue_seed"],
          seeds["dscr_seed"], seeds["pf_seed"], seeds["pa_seed"]))
    conn.commit()
    conn.close()
    return seeds


def seed_new_season_from_final_round(team_id, previous_season, new_season, previous_fatigue=None):
    """
    Convenience wrapper: pull a team's final-round OVR/PF/PA/D-Sqrt/Fatigue
    straight from team_round_ratings for `previous_season` and seed
    `new_season` from them. PF and PA here are the normalized 0-100 OVR
    components (pf_norm/pa_norm) -- the actual numbers feeding OVR -- not
    the raw season point totals. Fatigue is read from the stored
    cumulative fatigue_after column automatically; pass `previous_fatigue`
    explicitly only to override it (e.g. a team missing fatigue history).
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT ovr, pf_norm, pa_norm, d_sqrt_raw, fatigue_after FROM team_round_ratings
        WHERE team_id = ? AND season = ?
        ORDER BY round DESC LIMIT 1
    """, (team_id, previous_season)).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No rating history found for team {team_id} in season {previous_season}")
    fatigue = previous_fatigue if previous_fatigue is not None else (row["fatigue_after"] or 0.0)
    return store_carryover_seeds(
        team_id, new_season,
        prev_ovr=row["ovr"], prev_fatigue=fatigue,
        prev_dscr=row["d_sqrt_raw"], prev_pf=row["pf_norm"], prev_pa=row["pa_norm"],
    )


def blend_weights(round_num):
    """(X, Y) for the round about to be played. X=0 before matchday 1,
    increments by 1 after each matchday resolves, capped at 8/0 from
    matchday 9 onward (block no longer active)."""
    x = min(max(round_num - 1, 0), 8)
    return x, 8 - x


def blend_stat(new_value, seed_value, round_num):
    """The actual carryover blend: (NewSeason*X + Seed*Y) / 8. Only
    meaningful for OVR, Fatigue, DSCR (raw D-Sqrt), PF, and PA -- nothing
    else in the spec uses this."""
    x, y = blend_weights(round_num)
    new_value = new_value if new_value is not None else 0.0
    return (new_value * x + seed_value * y) / 8


def get_match_inputs(team_id, season, upcoming_round):
    """
    Blended OVR/PF/PA/DSCR for the match engine ahead of `upcoming_round`,
    for a team with stored carryover seeds. PF and PA are the normalized
    0-100 OVR components, matching what's stored as the seed -- blending a
    raw season total against a 0-100 seed would mix units. Returns the pure
    live stat unchanged once the blend window has closed (round > 9) or if
    the team has no seeds on file (e.g. season 1, nothing to carry over
    from). Fatigue is NOT included here -- call blend_stat() directly with
    the freshly computed fatigue() value for the specific upcoming matchup.
    """
    conn = get_connection()
    seeds = conn.execute(
        "SELECT * FROM season_carryover_seeds WHERE team_id = ? AND season = ?",
        (team_id, season),
    ).fetchone()
    live = conn.execute("""
        SELECT ovr, pf_norm, pa_norm, d_sqrt_raw FROM team_round_ratings
        WHERE team_id = ? AND season = ? AND round = ?
    """, (team_id, season, upcoming_round - 1)).fetchone()
    conn.close()

    live_ovr = live["ovr"] if live else None
    live_pf = live["pf_norm"] if live else None
    live_pa = live["pa_norm"] if live else None
    live_dscr = live["d_sqrt_raw"] if live else None

    if seeds is None:
        return {"ovr": live_ovr, "pf": live_pf, "pa": live_pa, "dscr": live_dscr}

    return {
        "ovr": blend_stat(live_ovr, seeds["ovr_seed"], upcoming_round),
        "pf": blend_stat(live_pf, seeds["pf_seed"], upcoming_round),
        "pa": blend_stat(live_pa, seeds["pa_seed"], upcoming_round),
        "dscr": blend_stat(live_dscr, seeds["dscr_seed"], upcoming_round),
    }


# ------------------------------------------------- regional/league strength --

def _team_ranks_snapshot(conn, season, round_num):
    """Global 1-160 rank by the PREVIOUS round's OVR, descending. Ties
    broken by team_id for stability. Un-rated teams default to BASELINE_OVR."""
    prior = {r["team_id"]: r["ovr"] for r in conn.execute(
        "SELECT team_id, ovr FROM team_round_ratings WHERE season = ? AND round = ?",
        (season, round_num - 1)).fetchall()}
    all_ids = _all_team_ids(conn, season)
    ordered = sorted(all_ids, key=lambda t: (-prior.get(t, BASELINE_OVR), t))
    return {t: i + 1 for i, t in enumerate(ordered)}


STRENGTH_MULTIPLIERS = {
    "rank": 2, "score": 2, "wins": 1, "dscr": 2, "elo": 1, "tot": 1, "gi": 1,
}
STRENGTH_TANH_STRETCH = 0.75  # curve amplitude: multiplier ranges (1-0.75, 1+0.75) = (0.25, 1.75)
STRENGTH_TANH_K_MULTIPLIER = 2  # curve steepness: k = this many std devs of the weighted sum


def _strength_raw_inputs(conn, season, round_num):
    """Per-team raw ingredients for both Regional and League Strength.
    Rank/Score use the previous round's OVR snapshot (the only genuinely
    circular ingredients); everything else, including GI, is a plain
    game-log stat with no dependency on RLStr at all -- no iteration
    needed."""
    team_ids = _all_team_ids(conn, season)
    teams_row = {r["team_id"]: r for r in conn.execute("SELECT * FROM teams").fetchall()}
    team_seasons = {r["team_id"]: r for r in conn.execute(
        "SELECT * FROM team_seasons WHERE season = ?", (season,)).fetchall()}
    ranks = _team_ranks_snapshot(conn, season, round_num)
    prior_ovr = {r["team_id"]: r["ovr"] for r in conn.execute(
        "SELECT team_id, ovr FROM team_round_ratings WHERE season = ? AND round = ?",
        (season, round_num - 1)).fetchall()}

    out = {}
    for tid in team_ids:
        games = _team_game_rows(conn, season, tid, round_num)
        walkovers = _team_walkover_rows(conn, season, tid, round_num)
        b = _points_buckets(games, walkovers)
        dscrs = [g["dscr"] for g in games if g["dscr"] is not None]
        d_sqrt = min(10.0, sqrt(max(sum(dscrs) / len(dscrs), 0))) if dscrs else 0.0
        gis = [g["interest_score"] for g in games if g["interest_score"] is not None]
        avg_gi = sum(gis) / len(gis) if gis else 0.0
        played_elos = [g["elo_after"] for g in games if g["elo_after"] is not None]
        elo = played_elos[-1] if played_elos else STARTING_ELO

        out[tid] = {
            "region": teams_row[tid]["region"],
            "division": team_seasons[tid]["league_division"],
            "rank": ranks[tid],
            "score": prior_ovr.get(tid, BASELINE_OVR),
            "intl_w": b["league_wins"] + b["cup_wins"] + b["playoff_finals_wins"],
            "dom_w": b["regional_wins"] + b["playoff_finals_wins"],
            "dscr": d_sqrt,
            "gi": avg_gi,
            "elo": elo,
            "tot": b["tot"],
        }
    return out


def _group_zscore(inputs, group_key, field_key):
    """Z-score of each group's average of `field_key`, across groups."""
    groups = defaultdict(list)
    for v in inputs.values():
        groups[v[group_key]].append(v[field_key])
    avgs = {g: sum(vals) / len(vals) for g, vals in groups.items()}
    mean = sum(avgs.values()) / len(avgs)
    std = statistics.pstdev(avgs.values())
    if std == 0:
        return {g: 0.0 for g in avgs}
    return {g: (avgs[g] - mean) / std for g in avgs}


def _strength_formula_breakdown(inputs, group_key, wins_field):
    """Full per-group breakdown of the seven z-scored components (rank
    flipped so 'better' is always positive), the weighted sum, and the
    tanh-squashed final multiplier centered on 1.0 with
    (1-STRETCH, 1+STRETCH) as asymptotic limits, never hit exactly."""
    z_rank = _group_zscore(inputs, group_key, "rank")
    z_score = _group_zscore(inputs, group_key, "score")
    z_wins = _group_zscore(inputs, group_key, wins_field)
    z_dscr = _group_zscore(inputs, group_key, "dscr")
    z_elo = _group_zscore(inputs, group_key, "elo")
    z_tot = _group_zscore(inputs, group_key, "tot")
    z_gi = _group_zscore(inputs, group_key, "gi")
    m = STRENGTH_MULTIPLIERS

    weighted = {}
    for g in z_rank:
        weighted[g] = (
            -z_rank[g] * m["rank"] + z_score[g] * m["score"] + z_wins[g] * m["wins"]
            + z_dscr[g] * m["dscr"] + z_elo[g] * m["elo"] + z_tot[g] * m["tot"]
            + z_gi[g] * m["gi"]
        )

    k = statistics.pstdev(weighted.values()) * STRENGTH_TANH_K_MULTIPLIER
    breakdown = {}
    for g in z_rank:
        final = 1.0 if k == 0 else 1.0 + STRENGTH_TANH_STRETCH * math.tanh(weighted[g] / k)
        breakdown[g] = {
            "z_rank": -z_rank[g], "z_ovr": z_score[g], "z_wins": z_wins[g],
            "z_dscr": z_dscr[g], "z_elo": z_elo[g], "z_tot": z_tot[g], "z_gi": z_gi[g],
            "weighted": weighted[g], "final": final,
        }
    return breakdown


def compute_strength_breakdown(season, round_num):
    """Returns (regional_breakdown_by_region, league_breakdown_by_division),
    each {group: {z_rank, z_ovr, z_wins, z_dscr, z_elo, z_tot, z_gi,
    weighted, final}} -- the full per-group ingredients behind
    compute_strength_scores()'s final-only output, for display (the
    dashboard's RL Strength tab)."""
    conn = get_connection()
    inputs = _strength_raw_inputs(conn, season, round_num)
    conn.close()

    regional_breakdown = _strength_formula_breakdown(inputs, "region", "intl_w")
    league_breakdown = _strength_formula_breakdown(inputs, "division", "dom_w")
    return regional_breakdown, league_breakdown


def compute_strength_scores(season, round_num):
    """Returns (regional_strength_by_region, league_strength_by_division).
    No iteration required -- this formula has no circular dependency on
    RLStr itself (unlike the AD-based version explored and reverted
    earlier)."""
    regional_breakdown, league_breakdown = compute_strength_breakdown(season, round_num)
    regional_strength = {g: v["final"] for g, v in regional_breakdown.items()}
    league_strength = {g: v["final"] for g, v in league_breakdown.items()}
    return regional_strength, league_strength


def seed_all_teams_for_new_season(previous_season, new_season, fatigue_by_team=None):
    """
    Bulk version of seed_new_season_from_final_round -- seeds every team
    that has rating history in `previous_season`. Fatigue is read
    automatically from each team's stored fatigue_after. fatigue_by_team
    is an optional {team_id: previous_fatigue} dict to override specific
    teams (e.g. one missing fatigue history for some reason).
    """
    conn = get_connection()
    fatigue_by_team = fatigue_by_team or {}
    team_ids = [r["team_id"] for r in conn.execute(
        "SELECT DISTINCT team_id FROM team_round_ratings WHERE season = ?", (previous_season,)
    ).fetchall()]
    fatigue_on_file = {r["team_id"]: r["fatigue_after"] for r in conn.execute("""
        SELECT team_id, fatigue_after FROM team_round_ratings
        WHERE season = ? AND round = (SELECT MAX(round) FROM team_round_ratings WHERE season = ?)
    """, (previous_season, previous_season)).fetchall()}
    conn.close()

    results = {}
    missing_fatigue = []
    for tid in team_ids:
        if tid not in fatigue_by_team and fatigue_on_file.get(tid) is None:
            missing_fatigue.append(tid)
        results[tid] = seed_new_season_from_final_round(
            tid, previous_season, new_season, previous_fatigue=fatigue_by_team.get(tid)
        )
    return results, missing_fatigue


# ------------------------------------------------------------- scheduling --
# Full 15-round round-robin schedule for a 16-team group (region or division),
# built from 4 pods of 4 teams arranged 2x2 (UL/UR/LL/LR). Verified exactly
# against real Season 9 games (Indigo's Forest vs Metro pod, round 2: all
# four predicted pairings AND the predicted home side both matched real data).

POD_IDENTITY = {'UL': ('U', 'L'), 'UR': ('U', 'R'), 'LL': ('D', 'L'), 'LR': ('D', 'R')}

POD_ROUND_DEFS = {
    1: [(4, 1), (3, 2)],   # (away_pos, home_pos): 4 at 1, 3 at 2
    8: [(1, 3), (2, 4)],
    15: [(2, 1), (4, 3)],
}

RELATION_PAIRS = {
    'Across': [('UL', 'UR'), ('LL', 'LR')],
    'Same': [('UL', 'LL'), ('UR', 'LR')],
    'Diag': [('UL', 'LR'), ('UR', 'LL')],
}

# round: (relation, pattern -- a list of (a,b) position pairs, or 'same' for
# the all-positions-match-their-own-number case -- host_direction)
CROSS_ROUND_DEFS = {
    2: ('Across', [(1, 3), (2, 4)], 'L'),
    3: ('Across', [(1, 4), (2, 3)], 'R'),
    4: ('Diag', [(1, 3), (2, 4)], 'L'),
    5: ('Diag', [(1, 4), (2, 3)], 'R'),
    6: ('Same', [(1, 3), (2, 4)], 'D'),
    7: ('Same', [(1, 4), (2, 3)], 'U'),
    9: ('Same', 'same', 'D'),
    10: ('Same', [(1, 2), (3, 4)], 'U'),
    11: ('Diag', 'same', 'L'),
    12: ('Diag', [(1, 2), (3, 4)], 'R'),
    13: ('Across', 'same', 'L'),
    14: ('Across', [(1, 2), (3, 4)], 'R'),
}


def _ref_other_pod(relation, pod_a, pod_b):
    """Which of the pair is the 'reference' pod whose position number is
    listed first in a pattern like '1v3' -- the L-column pod for Across/Diag,
    the U-row pod for Same."""
    axis = 1 if relation in ('Across', 'Diag') else 0
    target = 'L' if relation in ('Across', 'Diag') else 'U'
    if POD_IDENTITY[pod_a][axis] == target:
        return pod_a, pod_b
    return pod_b, pod_a


def generate_pod_schedule(pods):
    """
    pods: {'UL': [team1,team2,team3,team4], 'UR': [...], 'LL': [...], 'LR': [...]}
    each list already ordered by position 1-4 (as listed in the Conf/League tab).
    Returns {round_num: [(home, away), ...]} for rounds 1-15. `home`/`away`
    are whatever's in the pods lists (team_id, name -- caller's choice).
    """
    schedule = {}

    for rnd, pairs in POD_ROUND_DEFS.items():
        games = []
        for teams in pods.values():
            for away_pos, home_pos in pairs:
                games.append((teams[home_pos - 1], teams[away_pos - 1]))
        schedule[rnd] = games

    for rnd, (relation, pattern, host_dir) in CROSS_ROUND_DEFS.items():
        games = []
        for pod_a, pod_b in RELATION_PAIRS[relation]:
            ref, other = _ref_other_pod(relation, pod_a, pod_b)
            ref_teams, other_teams = pods[ref], pods[other]
            axis = 1 if host_dir in ('L', 'R') else 0
            ref_hosts = POD_IDENTITY[ref][axis] == host_dir
            pairs_full = ([(i, i) for i in range(1, 5)] if pattern == 'same'
                          else list(pattern) + [(b, a) for a, b in pattern])
            for a, b in pairs_full:
                ref_team, other_team = ref_teams[a - 1], other_teams[b - 1]
                games.append((ref_team, other_team) if ref_hosts else (other_team, ref_team))
        schedule[rnd] = games

    return schedule


# ---------------------------------------------------------------- RDS Cup --
# Ribbon/Dream/Star Cups: 64-seed Draw-and-Process (croquet-style) knockout.
# Draw is a standard single-elim bracket (seed K vs seed 65-K, round 1).
# Process uses a distinct round-1 seeding (confirmed against the real Cup
# tabs: every seed 1-16 pairs with a seed 49-64, matching the same teams
# that get a bye in the Draw for 48-team Cups). Both run 5 rounds (64 down
# to 2 remaining), then combine into a shared final stage.

# Process round-1 pairs, in bracket order (pair i's winner meets pair i+1's
# winner in round 2, standard adjacency from there in both brackets).
PROCESS_ROUND1_PAIRS = [
    (1, 49), (33, 17), (9, 57), (41, 25), (5, 53), (37, 21), (13, 61), (45, 29),
    (3, 51), (35, 19), (11, 59), (43, 27), (7, 55), (39, 23), (15, 63), (47, 31),
    (2, 50), (34, 18), (10, 58), (42, 26), (6, 54), (38, 22), (14, 62), (46, 30),
    (4, 52), (36, 20), (12, 60), (44, 28), (8, 56), (40, 24), (16, 64), (48, 32),
]


def _draw_round1_pairs():
    """
    Standard single-elim seed placement (the classic recursive 'reflection'
    method) rather than naive ascending K-vs-(65-K) order. The pairs
    themselves are identical either way (round 1 matched real results
    exactly under both), but only this ordering correctly encodes which
    round-1 pairs meet in round 2 onward -- confirmed by tracing real Ribbon
    Cup results (seed 3's actual round-2 opponent pair was exactly what this
    ordering predicts; naive ascending order predicts something else).
    """
    order = [1]
    while len(order) < 64:
        n = len(order) * 2 + 1
        order = [x for s in order for x in (s, n - s)]
    return [(order[i], order[i + 1]) for i in range(0, 64, 2)]


def generate_cup_bracket(seed_to_team, bracket_type):
    """
    seed_to_team: {seed_number(1-64): team_name_or_id}, missing/'bye' seeds
    treated as a bye. bracket_type: 'draw' or 'process'.

    Returns {round_num(1-5): [{'home':.., 'away':.., 'home_seed':.., 'away_seed':..,
    'bye': bool}, ...]}. A 'bye' entry has away=None; the home side auto-advances.
    Only round 1 is fully determined by seed; rounds 2-5 are structural
    placeholders (slot positions) since who reaches them depends on actual
    results not yet available.
    """
    def team_of(seed):
        t = seed_to_team.get(seed)
        return None if t in (None, 'bye') else t

    pairs = _draw_round1_pairs() if bracket_type == 'draw' else PROCESS_ROUND1_PAIRS
    home_is_lower_seed = (bracket_type == 'draw')  # lower seed number = higher/better seed

    bracket = {1: []}
    for seed_a, seed_b in pairs:
        team_a, team_b = team_of(seed_a), team_of(seed_b)
        if team_a is None and team_b is None:
            continue
        if team_a is None or team_b is None:
            survivor_seed = seed_b if team_a is None else seed_a
            survivor_team = team_b if team_a is None else team_a
            bracket[1].append({
                'home': survivor_team, 'away': None,
                'home_seed': survivor_seed, 'away_seed': None, 'bye': True,
            })
            continue
        if home_is_lower_seed:
            home_seed, away_seed = min(seed_a, seed_b), max(seed_a, seed_b)
        else:
            home_seed, away_seed = max(seed_a, seed_b), min(seed_a, seed_b)
        home_team = team_a if home_seed == seed_a else team_b
        away_team = team_b if home_seed == seed_a else team_a
        bracket[1].append({
            'home': home_team, 'away': away_team,
            'home_seed': home_seed, 'away_seed': away_seed, 'bye': False,
        })

    for rnd in range(2, 6):
        n_slots = len(pairs) // (2 ** (rnd - 1))
        bracket[rnd] = [{'home': None, 'away': None, 'home_seed': None, 'away_seed': None, 'bye': False}
                        for _ in range(n_slots)]

    return bracket


def resolve_mutual_stage(draw_finalists, process_finalists, seed_lookup):
    """
    draw_finalists / process_finalists: lists of 2 team names (the survivors
    after round 5 of each bracket). seed_lookup: {team_name: seed_number}
    (lower = better) for determining home in the shared final rounds.

    Returns {'semifinal': [...], 'final_note': str} describing the shared
    stage per the confirmed rules: 4 distinct -> Draw-pair semi + Process-pair
    semi, winners meet in final; 3 distinct (1 shared) -> shared team byes to
    final, other two play the lone semifinal; 2 distinct (same pair both
    sides) -> no semifinal, straight to final.
    """
    def home_away(team_x, team_y):
        if seed_lookup.get(team_x, 999) <= seed_lookup.get(team_y, 999):
            return team_x, team_y
        return team_y, team_x

    draw_set, process_set = set(draw_finalists), set(process_finalists)
    shared = draw_set & process_set

    if len(shared) == 2:
        home, away = home_away(*draw_finalists)
        return {
            'semifinal': [],
            'final_note': 'Draw and Process produced the same two finalists -- semifinal skipped, straight to final.',
            'final': {'home': home, 'away': away},
        }

    if len(shared) == 1:
        bye_team = next(iter(shared))
        other_draw = next(t for t in draw_finalists if t != bye_team)
        other_process = next(t for t in process_finalists if t != bye_team)
        home, away = home_away(other_draw, other_process)
        return {
            'semifinal': [{'home': home, 'away': away, 'bye_team': bye_team}],
            'final_note': f'{bye_team} reached the final two in both Draw and Process -- bye to the final.',
        }

    home1, away1 = home_away(*draw_finalists)
    home2, away2 = home_away(*process_finalists)
    return {
        'semifinal': [
            {'home': home1, 'away': away1, 'label': 'Draw finalists'},
            {'home': home2, 'away': away2, 'label': 'Process finalists'},
        ],
        'final_note': 'All four finalists distinct -- Draw pair and Process pair each play a semifinal; winners meet in the final.',
    }


# ---------------------------------------------------------------- PA Cup --
# 160-team Draw-and-Process ladder tournament. Seeds 1-32/33-64/65-96/97-160
# form 32 independent "ladder rows" -- row i = (161-i, 96+i, 97-i, 32+i, 33-i)
# for i=1..32 -- climbing sequentially: round1 is (161-i) vs (96+i), the
# proper seed-vs-mirror-seed pairing within the full tier-D range (97-160,
# no bye) -- e.g. row 1 is 160 vs 97, row 32 is 129 vs 128 -- NOT the naive
# first-half/second-half pairing (128+i vs 96+i) used before 2026-08-06,
# which was confirmed wrong (produced entirely incorrect PA Cup Draw Round 1
# matchups in real play, reverted). Round2 winner faces 97-i (tier C entrant,
# also mirrored within its own 32-seed range -- NOT 64+i); round3 winner
# faces 32+i (tier B entrant, unmirrored -- unchanged from before); round4
# winner faces 33-i (tier A entrant, mirrored -- NOT i). From round5 on, the
# 32 row-champions play a standard seeded bracket keyed on each row's final
# seed identity (33-i, not i) -- row-seed k vs row-seed 33-k in round5,
# standard progression after; this rule itself is unchanged, it just
# inherits the corrected row[4] value. Confirmed against the user's own
# explicit example, round1 through round5, for rows 1 and 2.

def pa_cup_ladder_rows():
    """32 rows, each [161-i, 96+i, 97-i, 32+i, 33-i] for i=1..32."""
    return [[161 - i, 96 + i, 97 - i, 32 + i, 33 - i] for i in range(1, 33)]


def pa_cup_round1_pairs(seed_to_team, home_is_lower_seed):
    """
    seed_to_team: {seed(1-160): team}. home_is_lower_seed: True for Draw
    (better/lower seed hosts), False for Process (worse/higher seed hosts).
    Returns a list of 32 dicts, one per ladder row, each with the row's
    round-1 matchup (always tier-D vs tier-D, seeds 97-160).
    """
    games = []
    for row in pa_cup_ladder_rows():
        seed_a, seed_b = row[0], row[1]  # 161-i, 96+i -- both real, tier D
        if home_is_lower_seed:
            home_seed, away_seed = min(seed_a, seed_b), max(seed_a, seed_b)
        else:
            home_seed, away_seed = max(seed_a, seed_b), min(seed_a, seed_b)
        games.append({
            'row': row, 'home_seed': home_seed, 'away_seed': away_seed,
            'home': seed_to_team.get(home_seed), 'away': seed_to_team.get(away_seed),
        })
    return games


def _pair_key(game):
    return frozenset([game['home'], game['away']])


def _pa_swap_pass(games, seed_to_team, violates_fn, home_is_lower_seed):
    """Shared swap-search engine, one pass over one bracket's round-1
    games against a single violation predicate. For every game that fails
    violates_fn, search same-half seeds (round 1's 64-team field splits
    into top half 97-128 / bottom half 129-160, and a swap must never
    cross that line -- e.g. #8 can never end up facing #7, only 9-16) for
    a replacement that doesn't itself violate, checked for BOTH the row
    being fixed and the candidate's own row, so a fix never creates a new
    violation elsewhere. Mutates games/seed_to_team in place. Returns a
    log of swaps made (and any conflicts left unresolved -- if no valid
    same-half swap exists, the original pairing stands)."""
    log = []

    def rebuild_row(row_idx):
        row = games[row_idx]['row']
        seed_a, seed_b = row[0], row[1]
        if home_is_lower_seed:
            home_seed, away_seed = min(seed_a, seed_b), max(seed_a, seed_b)
        else:
            home_seed, away_seed = max(seed_a, seed_b), min(seed_a, seed_b)
        games[row_idx] = {
            'row': row, 'home_seed': home_seed, 'away_seed': away_seed,
            'home': seed_to_team.get(home_seed), 'away': seed_to_team.get(away_seed),
        }

    TOP_HALF = range(97, 129)   # better half of round 1's 64-team field
    BOTTOM_HALF = range(129, 161)  # worse half

    for idx, game in enumerate(games):
        reason = violates_fn(game['home'], game['away'])
        if reason is None:
            continue

        worse_seed = max(game['home_seed'], game['away_seed'])
        other_seed_in_row = min(game['home_seed'], game['away_seed'])
        same_half = BOTTOM_HALF if worse_seed in BOTTOM_HALF else TOP_HALF
        # search worse-first, then better, but only within the same half
        search_order = [s for s in same_half if s > worse_seed] + \
                       [s for s in reversed(same_half) if s < worse_seed]

        swapped = False
        for candidate in search_order:
            other_team = seed_to_team[other_seed_in_row]
            incoming_team = seed_to_team[candidate]
            if violates_fn(other_team, incoming_team) is not None:
                continue

            candidate_row_idx = next(i for i, g in enumerate(games) if candidate in (g['row'][0], g['row'][1]))
            candidate_row = games[candidate_row_idx]['row']
            candidate_partner_seed = candidate_row[1] if candidate_row[0] == candidate else candidate_row[0]
            candidate_partner_team = seed_to_team[candidate_partner_seed]
            outgoing_team = seed_to_team[worse_seed]
            if violates_fn(candidate_partner_team, outgoing_team) is not None:
                continue

            seed_to_team[worse_seed], seed_to_team[candidate] = \
                seed_to_team[candidate], seed_to_team[worse_seed]
            log.append({'row': idx + 1, 'swapped_seed': worse_seed, 'with_seed': candidate, 'reason': reason})
            swapped = True
            break

        if swapped:
            rebuild_row(idx)
            rebuild_row(candidate_row_idx)
        else:
            log.append({'row': idx + 1, 'swapped_seed': None, 'with_seed': None,
                        'reason': f'{reason} -- no valid same-half swap found, original pairing stands'})

    return log


def resolve_pa_cup_conflicts(draw_games, draw_seed_to_team, process_games, process_seed_to_team,
                              team_region, team_division):
    """
    Mutates both draw_games/draw_seed_to_team and process_games/
    process_seed_to_team in place to remove round-1 conflicts, in this
    exact order (per explicit instruction):
      1. (up until round 6) Fix the Draw for same-region/same-division
         pairings.
      2. (up until the mutual quarterfinal) Fix the Process for any
         pairing that repeats a Draw pairing (checked against the Draw's
         now-final round-1 pairings from step 1).
      3. (up until round 6) Fix the Process for same-region/same-division
         pairings.
      4. Confirm both brackets satisfy all three rules in their final
         state (catches any interaction between steps 1-3, e.g. step 3
         accidentally reintroducing a duplicate step 2 already fixed).
      5. If no valid swap exists, the pairing is left as-is -- this always
         wins over the three rules above.
    Swap candidates are always restricted to the SAME half (top/bottom, by
    seed strength) as the team being replaced.

    team_region / team_division: {team_name: region_or_division}.
    Returns a log of swaps made (and any conflicts left unresolved), each
    entry tagged with which bracket ('Draw' or 'Process') it applies to.
    """
    def region_division_violation(team_a, team_b):
        if team_a is None or team_b is None:
            return None
        if team_division.get(team_a) == team_division.get(team_b):
            return 'same division'
        if team_region.get(team_a) == team_region.get(team_b):
            return 'same region'
        return None

    # 1. Draw's own region/division conflicts.
    draw_log = _pa_swap_pass(draw_games, draw_seed_to_team, region_division_violation, home_is_lower_seed=True)

    # 2. Process pairings that repeat a (now-final) Draw pairing.
    draw_pairs = {_pair_key(g) for g in draw_games}

    def duplicate_violation(team_a, team_b):
        if team_a is None or team_b is None:
            return None
        return 'repeat matchup (Draw)' if frozenset([team_a, team_b]) in draw_pairs else None

    dup_log = _pa_swap_pass(process_games, process_seed_to_team, duplicate_violation, home_is_lower_seed=False)

    # 3. Process's own region/division conflicts.
    region_log = _pa_swap_pass(process_games, process_seed_to_team, region_division_violation, home_is_lower_seed=False)

    log = (
        [dict(bracket='Draw', **e) for e in draw_log]
        + [dict(bracket='Process', **e) for e in dup_log]
        + [dict(bracket='Process', **e) for e in region_log]
    )

    # 4. Confirm -- re-check every game in its final state against all
    # three rules combined, catching anything the sequential passes above
    # didn't already log as unresolved.
    already_flagged = {(e['bracket'], e['row']) for e in log if e['swapped_seed'] is None}
    draw_pairs_final = {_pair_key(g) for g in draw_games}
    for bracket, games in (('Draw', draw_games), ('Process', process_games)):
        for idx, g in enumerate(games):
            row = idx + 1
            if (bracket, row) in already_flagged:
                continue
            reason = region_division_violation(g['home'], g['away'])
            if reason is None and bracket == 'Process' and frozenset([g['home'], g['away']]) in draw_pairs_final:
                reason = 'repeat matchup (Draw)'
            if reason is not None:
                log.append({'row': row, 'swapped_seed': None, 'with_seed': None,
                            'reason': f'{reason} -- found during final confirmation, no swap attempted',
                            'bracket': bracket})

    return log


# --------------------------------------------------------- weekly schedule --
# The NEW go-forward matchday structure (confirmed to replace the historical
# Excel-run order for everything from here on). Each week has up to three
# slots: Tuesday, Thursday, Weekend. "R"/"L" entries are Regional/League
# round numbers in THEIR OWN sequence (1-15), not the absolute round used
# in `games.round` -- only rounds 1-5 (R) and 1-2 (L) have a confirmed
# mapping to absolute rounds (see REGIONAL_ABS_ROUND / LEAGUE_ABS_ROUND
# below); anything past that has no games yet regardless.

WEEKLY_SCHEDULE = [
    {"week": 1, "Tue": None, "Thu": None, "Weekend": ("R", 1)},
    {"week": 2, "Tue": None, "Thu": None, "Weekend": ("R", 2)},
    {"week": 3, "Tue": ("RDS", "Draw", 1), "Thu": ("RDS", "Process", 1), "Weekend": ("R", 3)},
    {"week": 4, "Tue": ("RDS", "Draw", 2), "Thu": ("RDS", "Process", 2), "Weekend": ("L", 1)},
    # NOTE: a one-time calendar correction. The original week 5 (PA Draw/
    # Process round 1 + R4) and week 6 (L2, L3, R5) got out of sync with
    # what actually happened -- R4, L2, and R5 were already played before
    # this reconciliation was needed, leaving only PA-D-1st, PA-P-1st, and
    # L3 genuinely still pending. Per explicit instruction, this collapses
    # into a single special week 6 and applies ONLY this once; week 7
    # onward is untouched and this pattern should never recur.
    {"week": "6 (special)", "Tue": ("PA", "Draw", 1), "Thu": ("PA", "Process", 1), "Weekend": ("L", 3)},
    {"week": 7, "Tue": ("RDS", "Draw", 3), "Thu": ("RDS", "Process", 3), "Weekend": ("L", 4)},
    {"week": 8, "Tue": ("PA", "Draw", 2), "Thu": ("PA", "Process", 2), "Weekend": ("R", 6)},
    {"week": 9, "Tue": ("L", 5), "Thu": ("L", 6), "Weekend": ("R", 7)},
    {"week": 10, "Tue": ("RDS", "Draw", 4), "Thu": ("RDS", "Process", 4), "Weekend": ("L", 7)},
    {"week": 11, "Tue": ("PA", "Draw", 3), "Thu": ("PA", "Process", 3), "Weekend": ("R", 8)},
    {"week": 12, "Tue": ("L", 8), "Thu": ("L", 9), "Weekend": ("R", 9)},
    {"week": 13, "Tue": ("RDS", "Draw", 5), "Thu": ("RDS", "Process", 5), "Weekend": ("L", 10)},
    {"week": 14, "Tue": ("PA", "Draw", 4), "Thu": ("PA", "Process", 4), "Weekend": ("R", 10)},
    {"week": 15, "Tue": ("RDS", "SF", None), "Thu": ("RDS", "SF", None), "Weekend": ("L", 11)},
    {"week": 16, "Tue": ("PA", "Draw", 5), "Thu": ("PA", "Process", 5), "Weekend": ("R", 11)},
    {"week": 17, "Tue": ("L", 12), "Thu": ("L", 13), "Weekend": ("R", 12)},
    {"week": 18, "Tue": ("RDS", "Final", None), "Thu": ("RDS", "Final", None), "Weekend": ("L", 14)},
    {"week": 19, "Tue": ("PA", "Draw", 6), "Thu": ("PA", "Process", 6), "Weekend": ("R", 13)},
    {"week": 20, "Tue": ("PA", "Draw", 7), "Thu": ("PA", "Process", 7), "Weekend": ("L", 15)},
    {"week": 21, "Tue": ("PA", "QF", None), "Thu": ("PA", "QF", None), "Weekend": ("R", 14)},
    {"week": 22, "Tue": ("PA", "SF", None), "Thu": ("PA", "SF", None), "Weekend": ("R", 15)},
    {"week": 23, "Tue": ("PA", "Final", 1), "Thu": ("PA", "Final", 2), "Weekend": ("PA", "Final", 3)},
    {"week": 24, "Tue": ("RT", 1), "Thu": ("RT", 2), "Weekend": ("RT", 3)},
    {"week": 25, "Tue": ("RT", 4), "Thu": ("RT", 5), "Weekend": ("RT", 6)},
    {"week": 26, "Tue": ("RT", 7), "Thu": ("RT", 8), "Weekend": ("RT", 9)},
]

# Confirmed Regional/League round -> absolute `games.round` mapping (from
# cross-checking every Archive row-block against the migrated data). Only
# covers what's actually been played so far; deliberately doesn't guess
# beyond it.
REGIONAL_ABS_ROUND = {1: 1, 2: 2, 3: 3, 4: 6, 5: 9}
LEAGUE_ABS_ROUND = {1: 7, 2: 8}


def _event_is_played(conn, season, slot):
    """True if real game data exists for this weekly-schedule slot."""
    if slot is None:
        return True  # empty slot, nothing to wait on
    kind = slot[0]

    if kind in ("R", "L"):
        _, rnd = slot
        abs_round = abs_round_for_event(slot)
        game_type = "R" if kind == "R" else "L"
        n = conn.execute(
            "SELECT COUNT(*) c FROM games WHERE season=? AND round=? AND game_type=?",
            (season, abs_round, game_type),
        ).fetchone()["c"]
        return n > 0

    if kind == "RT":
        _, matchday = slot
        n = conn.execute(
            "SELECT COUNT(*) c FROM games WHERE season=? AND game_type='P' AND cup_round=?",
            (season, matchday),
        ).fetchone()["c"]
        return n > 0

    if kind == "RDS":
        _, bracket, cup_round = slot
        if bracket in ("SF", "Final"):
            return False  # mutual stage -- not modeled/played yet
        n = conn.execute(
            "SELECT COUNT(*) c FROM games WHERE cup_name IS NOT NULL AND cup_bracket=? AND cup_round=?",
            (bracket, cup_round),
        ).fetchone()["c"]
        return n > 0

    if kind == "PA":
        _, bracket, cup_round = slot
        if bracket in ("QF", "SF", "Final") or cup_round is None:
            return False  # not modeled/played yet
        n = conn.execute(
            "SELECT COUNT(*) c FROM games WHERE cup_name='PA' AND cup_bracket=? AND cup_round=?",
            (bracket, cup_round),
        ).fetchone()["c"]
        return n > 0

    return False


def next_matchday(season):
    """
    Walk the weekly schedule in order (Tue, Thu, Weekend within each week)
    and return the first slot with no real game data yet -- the actual
    next thing to play. Returns None if everything in WEEKLY_SCHEDULE has
    been played.
    """
    conn = get_connection()
    for week in WEEKLY_SCHEDULE:
        for day in ("Tue", "Thu", "Weekend"):
            slot = week[day]
            if slot is None:
                continue
            if not _event_is_played(conn, season, slot):
                conn.close()
                return {"week": week["week"], "day": day, "event": slot}
    conn.close()
    return None


def current_rank_lookup(season):
    """{team_id: rank(1-160)} using the latest round with any ratings, OVR
    descending -- matches DECKFIELD's Schedule tab importer, which is
    keyed on rank, not team_id or name."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT team_id, ovr FROM team_round_ratings
        WHERE season=? AND round=(SELECT MAX(round) FROM team_round_ratings WHERE season=?)
        ORDER BY ovr DESC
    """, (season, season)).fetchall()
    conn.close()
    return {r["team_id"]: i + 1 for i, r in enumerate(rows)}


def _real_bracket_winner(conn, cup_name, bracket, cup_round, team_a, team_b):
    """The actual winner of a specific real cup game, or None if it hasn't
    been played yet."""
    row = conn.execute("""
        SELECT g.result_a, ta.name a_name, tb.name b_name FROM games g
        JOIN teams ta ON ta.team_id=g.team_a JOIN teams tb ON tb.team_id=g.team_b
        WHERE g.cup_name=? AND g.cup_bracket=? AND g.cup_round=?
        AND ((ta.name=? AND tb.name=?) OR (ta.name=? AND tb.name=?))
    """, (cup_name, bracket, cup_round, team_a, team_b, team_b, team_a)).fetchone()
    if row is None:
        return None
    won_by_a = row["result_a"] in (2, 3)
    return row["a_name"] if won_by_a else row["b_name"]


def _rds_round_games(conn, cup, bracket, target_round):
    """[(home,away)] (team names) for a given RDS Cup round, resolved
    through real winners for every prior round. Returns None if a prior
    round isn't fully complete yet. target_round 1-5."""
    with open("cup_seeds_full.json") as f:
        seeds = json.load(f)
    seed_to_team = {int(k): v for k, v in seeds[cup].items()}
    team_to_seed = {v: k for k, v in seed_to_team.items() if v not in (None, "bye")}
    entries = generate_cup_bracket(seed_to_team, bracket.lower())[1]

    survivors = []
    for e in entries:
        if e["bye"]:
            survivors.append(e["home"])
        else:
            w = _real_bracket_winner(conn, cup, bracket, 1, e["home"], e["away"])
            if w is None:
                return None
            survivors.append(w)

    for rnd in range(2, target_round):
        next_survivors = []
        for i in range(0, len(survivors), 2):
            w = _real_bracket_winner(conn, cup, bracket, rnd, survivors[i], survivors[i + 1])
            if w is None:
                return None
            next_survivors.append(w)
        survivors = next_survivors

    if target_round == 1:
        return [(e["home"], e["away"]) for e in entries if not e["bye"]]

    home_is_lower = (bracket == "Draw")
    games = []
    for i in range(0, len(survivors), 2):
        team_a, team_b = survivors[i], survivors[i + 1]
        seed_a, seed_b = team_to_seed[team_a], team_to_seed[team_b]
        better_first = (seed_a < seed_b) == home_is_lower
        games.append((team_a, team_b) if better_first else (team_b, team_a))
    return games


def _pa_round1_games(conn):
    """(draw_games, process_games, draw_seed_to_team, process_seed_to_team,
    swap_log) -- the real, conflict-resolved PA Cup round-1 game dicts (from
    pa_cup_round1_pairs + resolve_pa_cup_conflicts), with BOTH brackets'
    seed_to_team reflecting their resolution swaps."""
    with open("pa_cup_seeds.json") as f:
        draw_seed_to_team = {int(k): v for k, v in json.load(f).items()}
    with open("pa_process_real_seeds_v2.json") as f:
        process_seed_to_team = {int(k): v for k, v in json.load(f).items()}
    team_region = {r["name"]: r["region"] for r in conn.execute("SELECT name, region FROM teams").fetchall()}
    team_division = {r["name"]: r["league_division"] for r in conn.execute("""
        SELECT t.name, ts.league_division FROM teams t
        JOIN team_seasons ts ON ts.team_id = t.team_id AND ts.season = 9
    """).fetchall()}

    draw_games = pa_cup_round1_pairs(draw_seed_to_team, home_is_lower_seed=True)
    process_games = pa_cup_round1_pairs(process_seed_to_team, home_is_lower_seed=False)
    swap_log = resolve_pa_cup_conflicts(draw_games, draw_seed_to_team, process_games, process_seed_to_team,
                                         team_region, team_division)
    return draw_games, process_games, draw_seed_to_team, process_seed_to_team, swap_log


def _pa_round_games(conn, bracket, target_round):
    """[(home,away)] (team names) for a given PA Cup round. Rounds 1-4 are
    each ladder row's independent climb (round1: seed pair; round2: round1
    winner vs the tier-C entrant; etc.). Rounds 5-7 are a standard bracket
    among the 32 row-champions. Returns None if a prior round isn't
    complete yet."""
    draw_games, process_games, draw_seeds, process_seeds, _ = _pa_round1_games(conn)
    round1_games = draw_games if bracket == "Draw" else process_games
    seed_to_team = draw_seeds if bracket == "Draw" else process_seeds

    if target_round <= 4:
        games = []
        for g in round1_games:
            row = g["row"]  # [161-i, 96+i, 97-i, 32+i, 33-i]
            team_a, team_b = g["home"], g["away"]
            for rnd in range(1, target_round):
                w = _real_bracket_winner(conn, "PA", bracket, rnd, team_a, team_b)
                if w is None:
                    return None
                entrant = seed_to_team[row[rnd + 1]]
                team_a, team_b = w, entrant
            games.append((team_a, team_b))
        return games

    # Rounds 5-7: every row must have finished round 4 first
    row_champions = []  # in row order, seed i (row[4]) used for bracket seeding
    for g in round1_games:
        row = g["row"]
        team_a, team_b = g["home"], g["away"]
        for rnd in range(1, 5):
            w = _real_bracket_winner(conn, "PA", bracket, rnd, team_a, team_b)
            if w is None:
                return None
            if rnd < 4:
                entrant = seed_to_team[row[rnd + 1]]
                team_a, team_b = w, entrant
        row_champions.append((w, row[4]))  # (team, original row-seed 1-32)

    # Round 5: standard bracket, row-seed k vs row-seed (33-k)
    champ_by_seed = {seed: team for team, seed in row_champions}
    round5_pairs = [(champ_by_seed[k], champ_by_seed[33 - k]) for k in range(1, 17)]
    if target_round == 5:
        return round5_pairs

    survivors = []
    for team_a, team_b in round5_pairs:
        w = _real_bracket_winner(conn, "PA", bracket, 5, team_a, team_b)
        if w is None:
            return None
        survivors.append(w)
    if target_round == 6:
        return [(survivors[i], survivors[i + 1]) for i in range(0, len(survivors), 2)]

    round6_pairs = [(survivors[i], survivors[i + 1]) for i in range(0, len(survivors), 2)]
    survivors2 = []
    for team_a, team_b in round6_pairs:
        w = _real_bracket_winner(conn, "PA", bracket, 6, team_a, team_b)
        if w is None:
            return None
        survivors2.append(w)
    if target_round == 7:
        return [(survivors2[i], survivors2[i + 1]) for i in range(0, len(survivors2), 2)]

    raise NotImplementedError(f"PA {bracket} round {target_round}: mutual quarterfinal+ not modeled yet.")


def _games_for_event(season, event):
    """Returns a list of (home_team_id, away_team_id) for a weekly-schedule
    event. Regional/League rounds use the pod schedule; RDS Cup rounds 1-5
    and PA Cup rounds 1-7 resolve through real prior-round winners where
    needed. Later cup stages (mutual semifinal/final) aren't modeled yet."""
    kind = event[0]
    conn = get_connection()
    name_to_dex = {r["name"]: r["team_id"] for r in conn.execute("SELECT team_id, name FROM teams").fetchall()}

    if kind in ("R", "L"):
        _, rnd = event
        conn.close()
        with open("conf_pods.json" if kind == "R" else "league_pods.json") as f:
            pod_blocks = json.load(f)
        games = []
        for group_name, pod_block in pod_blocks.items():
            pods = {label: info["teams"] for label, info in pod_block.items()}
            sched = generate_pod_schedule(pods)
            for home, away in sched[rnd]:
                games.append((name_to_dex[home], name_to_dex[away]))
        return games

    if kind == "RT":
        _, matchday = event
        with open("conf_pods.json") as f:
            pod_blocks = json.load(f)
        regions = list(pod_blocks.keys())
        conn.close()
        games = []
        for region in regions:
            region_games = regional_tournament_games(season, region, matchday)
            if region_games is None:
                raise ValueError(f"Regional Tournament {region} MD{matchday}: a prior matchday isn't complete yet.")
            games.extend(region_games)
        return [(name_to_dex[h], name_to_dex[a]) for h, a in games]

    if kind == "RDS":
        _, bracket, cup_round = event
        if bracket in ("SF", "Final"):
            conn.close()
            raise NotImplementedError("RDS mutual semifinal/final aren't modeled yet.")
        games = []
        for cup in ("Ribbon", "Dream", "Star"):
            cup_games = _rds_round_games(conn, cup, bracket, cup_round)
            if cup_games is None:
                conn.close()
                raise ValueError(f"RDS {cup} {bracket} round {cup_round}: a prior round isn't complete yet.")
            games.extend(cup_games)
        conn.close()
        return [(name_to_dex[h], name_to_dex[a]) for h, a in games]

    if kind == "PA":
        _, bracket, cup_round = event
        if bracket in ("QF", "SF", "Final") or cup_round is None:
            conn.close()
            raise NotImplementedError("PA mutual quarterfinal+ isn't modeled yet.")
        games = _pa_round_games(conn, bracket, cup_round)
        conn.close()
        if games is None:
            raise ValueError(f"PA {bracket} round {cup_round}: a prior round isn't complete yet.")
        return [(name_to_dex[h], name_to_dex[a]) for h, a in games]

    conn.close()
    raise ValueError(f"Unknown event kind: {kind}")


def export_matchday_for_deckfield(season, event=None):
    """
    Returns the next (or a specified) matchday's games in DECKFIELD's
    Schedule-tab paste format: tab-separated (confirmed against DECKFIELD's
    own parseSchedulePaste, which splits on '\\t'), one row per game,
    "away_rank\\thome_rank\\tadv" (adv is left blank -- it's DECKFIELD's own
    spread-modifier field, not something this engine computes), with a
    header row matching DECKFIELD's SCHEDULE_COLUMNS exactly since its
    importer defaults to expecting one. If `event` isn't given, uses
    next_matchday(). Returns (event_info, csv_text). event_info includes
    'abs_round' -- the value to put in the `round` column of the results
    CSV when reporting this matchday's outcomes back.
    """
    if event is None:
        info = next_matchday(season)
        if info is None:
            return None, ""
        event = info["event"]
    else:
        info = {"event": event}
    info["abs_round"] = abs_round_for_event(event)

    games = _games_for_event(season, event)
    ranks = current_rank_lookup(season)
    # Play order is worst-ranked team first, not the source bracket/pod
    # order (e.g. PA Cup's ladder-row order) -- sort each game by its
    # lowest-ranked (highest rank number) team, descending.
    games = sorted(games, key=lambda hg: max(ranks[hg[0]], ranks[hg[1]]), reverse=True)
    lines = ["Away Team Rank\tHome Team Rank\tAdv"]
    lines += [f"{ranks[away]}\t{ranks[home]}\t" for home, away in games]
    return info, "\n".join(lines)


_BATCH_SETTINGS_HEADER = "Game Type\tFormat\tDay\tRound Label\tRound #\tCup Name\tCup Bracket\tCup Round #"


def _week_day_for_event(event):
    """(week, day) of an event's first occurrence in WEEKLY_SCHEDULE, for
    events not reached via next_matchday() (which already knows its own
    day). Good enough for SF/Final/QF slots too -- those repeat the same
    event tuple across multiple days, but they're not exportable anyway
    (_games_for_event raises NotImplementedError for them)."""
    for week in WEEKLY_SCHEDULE:
        for day in ("Tue", "Thu", "Weekend"):
            if week[day] == event:
                return week["week"], day
    return None, None


def _batch_settings_row(game_type, agg, weekend, round_label, abs_round,
                         cup_name="", cup_bracket="", cup_round=""):
    fmt = "AGG (2-leg / Bo3)" if agg else "Single Game"
    day = "Weekend" if weekend else "Midweek"
    return "\t".join(str(v) for v in
                      (game_type, fmt, day, round_label, abs_round, cup_name, cup_bracket, cup_round))


def export_matchday_batches(season, event=None):
    """
    Like `export_matchday_for_deckfield`, but split into one or more
    per-cup "batches" -- everything DECKFIELD's Schedule Batch Settings
    panel needs to be filled in by pasting a single row (Game Type,
    Format, Day, Round Label, Round #, Cup Name, Cup Bracket, Cup Round #),
    alongside that batch's own matchups in the existing Away/Home rank
    format. RDS Cup matchdays play Ribbon, Dream, and Star simultaneously,
    but a DECKFIELD batch can only carry one Cup Name for every matchup
    pasted with it -- so RDS matchdays always split into three batches,
    one per cup, each with only that cup's games. Every other event kind
    (R/L/PA/RT) is just one batch.

    Returns (info, batches). info is the same as from
    `export_matchday_for_deckfield` (includes 'abs_round'). Each batch is
    {'label', 'settings_tsv' (header + one row, paste into Batch
    Settings), 'games' (list of {'away_rank','away_name','home_rank',
    'home_name'}, worst-ranked team first), 'matchups_tsv' (header + rows,
    paste into Import Matchups, same format/order as
    export_matchday_for_deckfield)}.
    """
    if event is None:
        info = next_matchday(season)
        if info is None:
            return None, []
        event = info["event"]
    else:
        info = {"event": event}
        info["week"], info["day"] = _week_day_for_event(event)
    info["abs_round"] = abs_round_for_event(event)
    weekend = info.get("day") == "Weekend"

    conn = get_connection()
    name_to_dex = {r["name"]: r["team_id"] for r in conn.execute("SELECT team_id, name FROM teams").fetchall()}
    dex_to_name = {v: k for k, v in name_to_dex.items()}
    ranks = current_rank_lookup(season)

    def build_batch(label, game_type, agg, cup_name, cup_bracket, cup_round, games):
        games = sorted(games, key=lambda hg: max(ranks[hg[0]], ranks[hg[1]]), reverse=True)
        settings_row = _batch_settings_row(game_type, agg, weekend, label, info["abs_round"],
                                            cup_name, cup_bracket, cup_round)
        matchup_lines = ["Away Team Rank\tHome Team Rank\tAdv"]
        matchup_lines += [f"{ranks[away]}\t{ranks[home]}\t" for home, away in games]
        return {
            "label": label,
            "settings_tsv": _BATCH_SETTINGS_HEADER + "\n" + settings_row,
            "matchups_tsv": "\n".join(matchup_lines),
            "games": [{
                "away_rank": ranks[away], "away_name": dex_to_name[away],
                "home_rank": ranks[home], "home_name": dex_to_name[home],
            } for home, away in games],
        }

    kind = event[0]
    if kind == "RDS":
        _, bracket, cup_round = event
        batches = []
        for cup in ("Ribbon", "Dream", "Star"):
            cup_games = _rds_round_games(conn, cup, bracket, cup_round)
            if cup_games is None:
                conn.close()
                raise ValueError(f"RDS {cup} {bracket} round {cup_round}: a prior round isn't complete yet.")
            games = [(name_to_dex[h], name_to_dex[a]) for h, a in cup_games]
            agg = bracket in ("SF", "Final")
            label = f"{cup} {bracket} R{cup_round}" if cup_round is not None else f"{cup} {bracket}"
            batches.append(build_batch(label, "Cup", agg, cup, bracket, cup_round if cup_round is not None else "", games))
        conn.close()
        return info, batches

    if kind == "PA":
        _, bracket, cup_round = event
        label = f"PA {bracket} R{cup_round}" if cup_round is not None else f"PA {bracket}"
        agg = bracket in ("QF", "SF", "Final") or cup_round is None
        games = _games_for_event(season, event)
        batch = build_batch(label, "Cup", agg, "PA", bracket, cup_round if cup_round is not None else "", games)
        conn.close()
        return info, [batch]

    if kind == "RT":
        _, matchday = event
        label = f"RT{matchday}"
        agg = matchday != 1
        games = _games_for_event(season, event)
        batch = build_batch(label, "Playoffs", agg, "", "", "", games)
        conn.close()
        return info, [batch]

    # R / L
    _, rnd = event
    label = f"{kind}{rnd}"
    game_type = "Regional" if kind == "R" else "League"
    games = _games_for_event(season, event)
    batch = build_batch(label, game_type, False, "", "", "", games)
    conn.close()
    return info, [batch]


def latest_played_schedule_round(season):
    """{'regional': N, 'league': N} -- the highest Regional/League round
    number (in their own 1-15 sequence) that actually has real game data,
    checked against the database for every round 1-15 (via the full
    schedule mapping), not just the historically-confirmed subset -- so
    this keeps advancing correctly as future rounds actually get played."""
    conn = get_connection()

    def latest(kind, game_type):
        played = []
        for rnd in range(1, 16):
            abs_round = abs_round_for_event((kind, rnd))
            n = conn.execute(
                "SELECT COUNT(*) c FROM games WHERE season=? AND round=? AND game_type=?",
                (season, abs_round, game_type),
            ).fetchone()["c"]
            if n > 0:
                played.append(rnd)
        return max(played) if played else None

    result = {"regional": latest("R", "R"), "league": latest("L", "L")}
    conn.close()
    return result


REGION_DISPLAY_NAME = {"LilyValley": "Lily Valley"}


def region_display_name(region):
    """The name DECKFIELD actually expects -- its own REGION_COLORS keys
    use 'Lily Valley' (with a space), while this database's internal
    region field is the space-free 'LilyValley'. Every other region name
    is already identical either way."""
    return REGION_DISPLAY_NAME.get(region, region)


def pa_cup_round1_seeding():
    """{draw_round1, process_round1, swap_log, process_is_placeholder} in
    the exact shape the dashboard's PA_CUP_DATA constant needs -- the
    live, conflict-resolved round-1 structural seeding (source of truth:
    pa_cup_ladder_rows() + resolve_pa_cup_conflicts()), with each game
    dict carrying home_dex/away_dex for display. Regenerate this whenever
    pa_cup_ladder_rows() or either seed JSON changes -- PA_CUP_DATA is a
    static embedded constant, not derived from the database, so nothing
    else will pick up a formula fix automatically."""
    conn = get_connection()
    name_to_dex = {r["name"]: r["team_id"] for r in conn.execute("SELECT team_id, name FROM teams").fetchall()}
    draw_games, process_games, _, _, swap_log = _pa_round1_games(conn)
    conn.close()

    def _with_dex(games):
        return [dict(g, home_dex=name_to_dex.get(g["home"]), away_dex=name_to_dex.get(g["away"]))
                for g in games]

    return {
        "draw_round1": _with_dex(draw_games),
        "process_round1": _with_dex(process_games),
        "swap_log": swap_log,
        "process_is_placeholder": False,
    }


def pa_cup_real_results(season):
    """
    {bracket: {cup_round_str: [{winner, loser, winner_dex, loser_dex,
    winner_score, loser_score}, ...]}} for every PA Cup round that has
    real games so far -- source data for the dashboard PA Cup tab's
    real-results display (mirrors the shape RDS Cup's CUP_REAL_RESULTS
    already uses, minus the outer cup-name layer, since PA is a single
    cup). Seed/home-away info isn't included here -- the dashboard
    already has that structurally, from PA_CUP_DATA for round 1 and from
    a generated round-2+ preview (see `_pa_round_games`) for later rounds.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT g.cup_bracket, g.cup_round, ta.name a_name, tb.name b_name,
               ta.team_id a_dex, tb.team_id b_dex, g.result_a, g.pf_a, g.pa_a
        FROM games g
        JOIN teams ta ON ta.team_id = g.team_a
        JOIN teams tb ON tb.team_id = g.team_b
        WHERE g.season = ? AND g.cup_name = 'PA' AND g.cup_bracket IS NOT NULL AND g.cup_round IS NOT NULL
        ORDER BY g.cup_round
    """, (season,)).fetchall()
    conn.close()

    results = {"Draw": {}, "Process": {}}
    for r in rows:
        a_won = r["result_a"] in (2, 3)
        winner, loser = (r["a_name"], r["b_name"]) if a_won else (r["b_name"], r["a_name"])
        winner_dex, loser_dex = (r["a_dex"], r["b_dex"]) if a_won else (r["b_dex"], r["a_dex"])
        winner_score, loser_score = (r["pf_a"], r["pa_a"]) if a_won else (r["pa_a"], r["pf_a"])
        results[r["cup_bracket"]].setdefault(str(r["cup_round"]), []).append({
            "winner": winner, "loser": loser, "winner_dex": winner_dex, "loser_dex": loser_dex,
            "winner_score": winner_score, "loser_score": loser_score,
        })
    return results


def pa_cup_round_preview(season, target_round):
    """{bracket: [{home,away,home_seed,away_seed,home_dex,away_dex}]} for a
    PA Cup round that hasn't been fully played yet but whose prior
    round(s) are complete -- the "what's coming up next" preview, same
    idea as RDS Cup's RDS_ROUND3. A bracket's value is None if a prior
    round isn't complete yet (mirrors _pa_round_games' own contract) or if
    target_round is beyond what's modeled (8+, the mutual quarterfinal
    stage -- see _pa_round_games's NotImplementedError)."""
    conn = get_connection()
    name_to_dex = {r["name"]: r["team_id"] for r in conn.execute("SELECT team_id, name FROM teams").fetchall()}
    _, _, draw_seeds, process_seeds, _ = _pa_round1_games(conn)
    seed_lookup = {"Draw": draw_seeds, "Process": process_seeds}

    preview = {}
    for bracket in ("Draw", "Process"):
        team_to_seed = {v: k for k, v in seed_lookup[bracket].items() if v not in (None, "bye")}
        games = _pa_round_games(conn, bracket, target_round)
        if games is None:
            preview[bracket] = None
            continue
        home_is_lower = (bracket == "Draw")
        entries = []
        for team_a, team_b in games:
            seed_a, seed_b = team_to_seed[team_a], team_to_seed[team_b]
            better_first = (seed_a < seed_b) == home_is_lower
            home, away = (team_a, team_b) if better_first else (team_b, team_a)
            entries.append({
                "home": home, "away": away,
                "home_seed": team_to_seed[home], "away_seed": team_to_seed[away],
                "home_dex": name_to_dex.get(home), "away_dex": name_to_dex.get(away),
            })
        preview[bracket] = entries
    conn.close()
    return preview


def rank_elo_history(season):
    """
    Per-team rank and Elo history, one checkpoint per completed
    weekly-schedule event -- source data for the dashboard's Rank/Elo
    History tab. Mirrors how the S9 workbook tracked this by hand
    (Rankings!CG:CT for rank, Calcs!ANU:AOF for Elo), at two checkpoint
    granularities:
      - Elo checkpoints: one per individual event -- R/L keep their own
        1-15 numbering, RT its own matchday numbering, and every RDS/PA
        Draw or Process round gets its own sequential 'S' label (Calcs'
        S1/S2/S3/S4 pattern: S1/S2 = RDS round 1 Draw/Process, S3/S4 =
        RDS round 2 Draw/Process, and so on as more cup rounds are played).
      - Rank checkpoints: mostly the same, except it leads with an 'S8 End'
        checkpoint (Rankings!CT, the season-opening snapshot, no abs_round
        of its own) and within the historical portion (abs_round <= the
        last `_CONFIRMED_ABS_ROUND` value) a cup round's Draw and Process
        collapse into one 'SC' checkpoint using the later of the two
        abs_rounds (Rankings' SC1/SC2 pattern) -- that's how the S9
        workbook actually tracked it, and isn't worth re-deriving
        differently now. Everything from there on (per explicit
        instruction) gets its own column same as Elo, reusing the exact
        same 'S' label Elo assigns that abs_round -- Regional/League/RT
        events are never merged either way.
    Both are driven by `full_schedule_abs_round_mapping()` (the same
    abs_round assignment everything else in this engine uses) rather than
    WEEKLY_SCHEDULE's week/day placement directly, since the historical
    portion's actual play order doesn't necessarily match WEEKLY_SCHEDULE's
    day slots -- only the confirmed abs_round numbering does.

    Only includes abs_rounds that actually have computed ratings
    (team_round_ratings), so this grows automatically as more matchdays
    are played -- nothing here needs updating by hand when new results
    come in via `add-results`.
    """
    conn = get_connection()
    latest_round = conn.execute(
        "SELECT MAX(round) m FROM team_round_ratings WHERE season=?", (season,)
    ).fetchone()["m"]
    if latest_round is None:
        conn.close()
        return {"rank_checkpoints": [], "elo_checkpoints": [], "teams": []}

    events_by_round = {abs_r: event for event, abs_r in full_schedule_abs_round_mapping().items()}
    rounds = sorted(r for r in events_by_round if r <= latest_round)

    def plain_label(event):
        kind = event[0]
        if kind in ("R", "L"):
            return f"{kind}{event[1]}"
        if kind == "RT":
            return f"RT{event[1]}"
        return None  # RDS/PA cup events get sequential 'S'/'SC' labels below

    elo_checkpoints = []
    s_seq = 0
    for r in rounds:
        lbl = plain_label(events_by_round[r])
        if lbl is None:
            s_seq += 1
            lbl = f"S{s_seq}"
        elo_checkpoints.append({"label": lbl, "abs_round": r})

    # Draw/Process only merge into one rank checkpoint within the historical
    # portion (matching the S9 workbook's own SC1/SC2 columns) -- every cup
    # round from here on gets its own column, same granularity as Elo.
    historical_cutoff = max(_CONFIRMED_ABS_ROUND.values())
    elo_label_by_round = {cp["abs_round"]: cp["label"] for cp in elo_checkpoints}

    # "S8 End" -- the season-opening snapshot from Rankings!CT, copied
    # verbatim same as the other historical checkpoints below. No abs_round
    # of its own (it predates round 1), so abs_round 0 is used as a sentinel
    # -- safe since verbatim_ranks always has an entry for this label, and
    # the lookup below never falls through to a real-round query for it.
    rank_checkpoints = [{"label": "S8 End", "abs_round": 0}]
    sc_seq = 0
    i = 0
    while i < len(rounds):
        r = rounds[i]
        event = events_by_round[r]
        lbl = plain_label(event)
        if lbl is not None:
            rank_checkpoints.append({"label": lbl, "abs_round": r})
            i += 1
            continue
        kind, bracket, cup_round = event
        merged = False
        if r <= historical_cutoff and i + 1 < len(rounds):
            next_r = rounds[i + 1]
            next_event = events_by_round[next_r]
            if (next_r <= historical_cutoff and next_event[0] == kind and next_event[2] == cup_round
                    and {bracket, next_event[1]} == {"Draw", "Process"}):
                sc_seq += 1
                rank_checkpoints.append({"label": f"SC{sc_seq}", "abs_round": next_r})
                i += 2
                merged = True
        if not merged:
            rank_checkpoints.append({"label": elo_label_by_round[r], "abs_round": r})
            i += 1

    team_names = {r["team_id"]: r["name"] for r in conn.execute("SELECT team_id, name FROM teams").fetchall()}
    name_to_id = {name: team_id for team_id, name in team_names.items()}

    def ranks_at(abs_round):
        rows = conn.execute("""
            SELECT team_id FROM team_round_ratings
            WHERE season=? AND round=? ORDER BY ovr DESC
        """, (season, abs_round)).fetchall()
        return {row["team_id"]: i + 1 for i, row in enumerate(rows)}

    # The historical checkpoints (everything up through SC1) are the S9
    # workbook's own hand-tracked Rankings!CG:CT values, copied verbatim --
    # this engine's OVR-based rank doesn't reproduce them (the workbook's
    # own rank column was frozen at an older formula version, see CLAUDE.md).
    # SC2 (the last historical-labeled checkpoint) and everything after use
    # this engine's live computed rank, per explicit instruction.
    with open("rank_history_verbatim.json") as f:
        verbatim_ranks = json.load(f)

    rank_by_round = {}
    for cp in rank_checkpoints:
        if cp["label"] in verbatim_ranks:
            rank_by_round[cp["abs_round"]] = {name_to_id[name]: rank for name, rank in verbatim_ranks[cp["label"]].items()}
        else:
            rank_by_round[cp["abs_round"]] = ranks_at(cp["abs_round"])

    games_by_round = {}
    for g in conn.execute("SELECT round, team_a, team_b, elo_a_after, elo_b_after FROM games WHERE season=?", (season,)).fetchall():
        games_by_round.setdefault(g["round"], []).append(g)

    elo_by_round = {}
    current_elo = {}
    for r in rounds:
        for g in games_by_round.get(r, []):
            current_elo[g["team_a"]] = g["elo_a_after"]
            current_elo[g["team_b"]] = g["elo_b_after"]
        elo_by_round[r] = dict(current_elo)

    teams = []
    for team_id, name in team_names.items():
        teams.append({
            "team_id": team_id, "name": name,
            "rank_history": [rank_by_round[cp["abs_round"]].get(team_id) for cp in rank_checkpoints],
            "elo_history": [elo_by_round[cp["abs_round"]].get(team_id) for cp in elo_checkpoints],
        })

    conn.close()
    return {
        "rank_checkpoints": rank_checkpoints,
        "elo_checkpoints": elo_checkpoints,
        "teams": teams,
    }


def export_teams_for_deckfield(season):
    """
    Full team roster in DECKFIELD's expected paste format (tab-separated,
    one row per team, ranked best to worst): Team Rank, DEX #, Region,
    Team Name, League Division, Accolades, Fatigue, Climate, PF, PA, DSCR,
    Skill Rating, Elo, Rating Grade, Region Record, League Record, Cup
    Record, Overall Record, Primary Type, Secondary Type.

    Secondary Type is a fresh random 1-18 assigned by THIS export, not read
    from anywhere -- the dashboard owns generating it, new every time this
    runs (i.e. new each matchday, since that's when the export is
    regenerated), not DECKFIELD itself.
    PF/PA/Skill Rating use the normalized 0-100 OVR components (same
    numbers the dashboard's Rankings tab shows), not raw season totals.
    DSCR is the exception -- DECKFIELD's own Eye Test Bonus formula
    expects a raw 0-10 D-Sqrt value (its placeholder TEAM_DSCR is 7, not
    70), so this uses d_sqrt_raw, not the normalized dscr_comp component.
    """
    conn = get_connection()
    latest_round = conn.execute(
        "SELECT MAX(round) m FROM team_round_ratings WHERE season=?", (season,)
    ).fetchone()["m"]
    rows = conn.execute("""
        SELECT r.*, t.name, t.region, t.primary_type, t.accolades, t.team_id AS dex,
               ts.league_division
        FROM team_round_ratings r
        JOIN teams t ON t.team_id = r.team_id
        JOIN team_seasons ts ON ts.team_id = r.team_id AND ts.season = r.season
        WHERE r.season = ? AND r.round = ?
        ORDER BY r.ovr DESC
    """, (season, latest_round)).fetchall()

    def records_for(team_id):
        games = conn.execute("""
            SELECT game_type, result_a, team_a, team_b FROM games
            WHERE season=? AND round<=? AND (team_a=? OR team_b=?)
        """, (season, latest_round, team_id, team_id)).fetchall()
        walkovers = conn.execute("""
            SELECT game_type, result FROM walkovers WHERE season=? AND round<=? AND team_id=?
        """, (season, latest_round, team_id)).fetchall()
        buckets = {"R": [0, 0], "L": [0, 0], "S": [0, 0], "PF": [0, 0]}
        overall = [0, 0]
        for g in games:
            result = g["result_a"] if g["team_a"] == team_id else 3 - g["result_a"]
            won = result in (2, 3)
            key = "PF" if g["game_type"] in ("P", "F") else g["game_type"]
            buckets[key][0 if won else 1] += 1
            overall[0 if won else 1] += 1
        for w in walkovers:
            won = w["result"] in (2, 3)
            key = "PF" if w["game_type"] in ("P", "F") else w["game_type"]
            buckets[key][0 if won else 1] += 1
            overall[0 if won else 1] += 1
        fmt = lambda p: f"{p[0]}-{p[1]}"
        return fmt(overall), fmt(buckets["R"]), fmt(buckets["L"]), fmt(buckets["S"])

    def raw_elo(team_id):
        row = conn.execute("""
            SELECT elo_a_after, elo_b_after, team_a FROM games
            WHERE season=? AND round<=? AND (team_a=? OR team_b=?)
            ORDER BY round DESC LIMIT 1
        """, (season, latest_round, team_id, team_id)).fetchone()
        if row is None:
            return None
        return row["elo_a_after"] if row["team_a"] == team_id else row["elo_b_after"]

    teams_out = []
    for rank, r in enumerate(rows, start=1):
        overall, regional, league, cup = records_for(r["dex"])
        teams_out.append({
            "rank": rank, "dex": r["dex"], "region": region_display_name(r["region"]), "name": r["name"],
            "division": r["league_division"],
            "accolades": "; ".join(
                " ".join(entry.split())
                for entry in (r["accolades"] or "").split("\n\n") if entry.strip()
            ),
            "fatigue": round(r["fatigue_after"], 1) if r["fatigue_after"] is not None else "",
            "climate": round(r["climate"], 2), "pf": round(r["pf_norm"], 2),
            "pa": round(r["pa_norm"], 2), "dscr": round(r["d_sqrt_raw"], 2),
            "skill_rating": round(r["ovr"], 2), "elo": round(raw_elo(r["dex"]) or 0),
            "grade": r["grade"], "region_record": regional, "league_record": league,
            "cup_record": cup, "overall_record": overall, "primary_type": r["primary_type"],
            "secondary_type": random.randint(1, 18),
        })
    conn.close()

    header = ["Team Rank", "DEX #", "Region", "Team Name", "League Division", "Accolades",
              "Fatigue", "Climate", "PF", "PA", "DSCR", "Skill Rating", "Elo", "Rating Grade",
              "Region Record", "League Record", "Cup Record", "Overall Record",
              "Primary Type", "Secondary Type"]
    lines = ["\t".join(header)]
    for t in teams_out:
        lines.append("\t".join(str(x) for x in [
            t["rank"], t["dex"], t["region"], t["name"], t["division"], t["accolades"],
            t["fatigue"], t["climate"], t["pf"], t["pa"], t["dscr"], t["skill_rating"],
            t["elo"], t["grade"], t["region_record"], t["league_record"],
            t["cup_record"], t["overall_record"], t["primary_type"], t["secondary_type"],
        ]))
    return teams_out, "\n".join(lines)


# Historical absolute-round assignments, confirmed against real Archive
# data (see REGIONAL_ABS_ROUND/LEAGUE_ABS_ROUND above for the R/L subset).
# Everything not in this dict gets the next sequential integer by walking
# WEEKLY_SCHEDULE in order -- the same order next_matchday() uses -- so
# "what absolute round is event X" stays consistent with "what's next".
_CONFIRMED_ABS_ROUND = {
    ("R", 1): 1, ("R", 2): 2, ("R", 3): 3, ("R", 4): 6, ("R", 5): 9,
    ("L", 1): 7, ("L", 2): 8,
    ("RDS", "Draw", 1): 4, ("RDS", "Process", 1): 5,
    ("RDS", "Draw", 2): 10, ("RDS", "Process", 2): 11,
}


def _schedule_event_key(week, day, slot):
    """Unique key for a weekly-schedule slot. Numbered cup rounds and R/L
    rounds are naturally unique by (kind, ..., round). Two-legged-tie and
    best-of-three slots (SF/Final/QF, cup_round=None) repeat the same
    (kind, bracket, None) across Tue/Thu/Weekend, so those need week+day
    folded into the key to stay distinct -- otherwise leg 1 and leg 2 of
    the same tie would collapse onto a single absolute round."""
    if slot[0] in ("R", "L", "RT"):
        return (slot[0], slot[1])
    kind, bracket, cup_round = slot
    if cup_round is None:
        return (kind, bracket, week, day)
    return (kind, bracket, cup_round)


def full_schedule_abs_round_mapping():
    """{event_key: absolute_round} for the entire WEEKLY_SCHEDULE -- the
    confirmed historical value where real Archive data established one,
    the next sequential integer (continuing from the historical max)
    everywhere else. This is what determines which absolute round number
    to use when logging a new game via the CSV format."""
    mapping = dict(_CONFIRMED_ABS_ROUND)
    next_abs = max(_CONFIRMED_ABS_ROUND.values()) + 1
    for week in WEEKLY_SCHEDULE:
        for day in ("Tue", "Thu", "Weekend"):
            slot = week[day]
            if slot is None:
                continue
            key = _schedule_event_key(week["week"], day, slot)
            if key in mapping:
                continue
            mapping[key] = next_abs
            next_abs += 1
    return mapping


def abs_round_for_event(event):
    """The absolute round number to use in games.round for a given
    weekly-schedule event tuple, e.g. ('R', 6) or ('PA', 'Draw', 2)."""
    mapping = full_schedule_abs_round_mapping()
    if event[0] in ("R", "L", "RT"):
        return mapping[(event[0], event[1])]
    if event[2] is not None:
        return mapping[(event[0], event[1], event[2])]
    # SF/Final/QF: find this event's actual week/day to resolve the key
    for week in WEEKLY_SCHEDULE:
        for day in ("Tue", "Thu", "Weekend"):
            slot = week[day]
            if slot == event:
                return mapping[_schedule_event_key(week["week"], day, slot)]
    raise ValueError(f"Event {event} not found in WEEKLY_SCHEDULE")


# ---------------------------------------------------- Regional Tournament --
# End-of-season postseason, one per region, seeded 1-16 by final Regional
# Standings. 4 independent "lanes" (mirroring the PA Cup ladder pattern):
# lane = [MD1 away-seed, MD1 home-seed, MD2/3 entrant, MD4/5 entrant].
# MD1 is a single game (better seed hosts). MD2/3 and MD4/5 are two-legged
# ties: leg 1 hosted by the worse-seeded of the two teams, leg 2 by the
# better-seeded -- using each team's own original seed, confirmed to also
# govern the semifinal (MD6/7) and final (MD8/9), not a separate rule.
# Semifinal pairs lane 4's survivor vs lane 1's, lane 2's vs lane 3's.
# Games are Playoffs type ('P').

REGIONAL_TOURNAMENT_LANES = [
    [16, 9, 8, 1],
    [15, 10, 7, 2],
    [14, 11, 6, 3],
    [13, 12, 5, 4],
]


def regional_standings_seeds(season, region):
    """{seed(1-16): team_name} for a region's postseason, from final
    Regional Standings (the same W-L -> RP -> H2H -> TB sort as the
    dashboard's Standings tab)."""
    conn = get_connection()
    teams = conn.execute("""
        SELECT t.team_id, t.name FROM teams t WHERE t.region = ?
    """, (region,)).fetchall()
    team_ids = [t["team_id"] for t in teams]
    id_to_name = {t["team_id"]: t["name"] for t in teams}

    latest_round = conn.execute(
        "SELECT MAX(round) m FROM team_round_ratings WHERE season=?", (season,)
    ).fetchone()["m"]

    def record_and_stats(team_id):
        games = conn.execute("""
            SELECT result_a, team_a, team_b, dscr_a, dscr_b FROM games
            WHERE season=? AND round<=? AND game_type='R' AND (team_a=? OR team_b=?)
        """, (season, latest_round, team_id, team_id)).fetchall()
        walkovers = conn.execute("""
            SELECT result FROM walkovers
            WHERE season=? AND round<=? AND game_type='R' AND team_id=?
        """, (season, latest_round, team_id)).fetchall()
        w = l = 0
        dscrs = []
        log = []
        for g in games:
            is_a = g["team_a"] == team_id
            result = g["result_a"] if is_a else 3 - g["result_a"]
            won = result in (2, 3)
            w, l = w + (1 if won else 0), l + (0 if won else 1)
            own_dscr = g["dscr_a"] if is_a else g["dscr_b"]
            if own_dscr is not None:
                dscrs.append(own_dscr)
            opp = g["team_b"] if is_a else g["team_a"]
            log.append((opp, result))
        for wo in walkovers:
            won = wo["result"] in (2, 3)
            w, l = w + (1 if won else 0), l + (0 if won else 1)
        rp_row = conn.execute(
            "SELECT rp FROM team_round_ratings WHERE season=? AND round=? AND team_id=?",
            (season, latest_round, team_id),
        ).fetchone()
        rp = rp_row["rp"] if rp_row else 0
        dscr_avg = sum(dscrs) / len(dscrs) if dscrs else 0
        return w, l, rp, dscr_avg, log

    stats = {tid: record_and_stats(tid) for tid in team_ids}
    conn.close()

    def wl(tid):
        return stats[tid][0], stats[tid][1]

    def rp(tid):
        return stats[tid][2]

    def tb(tid):
        return stats[tid][3]  # DSCR average

    def h2h_record(a, b):
        w = l = 0
        for opp, result in stats[a][4]:
            if opp == b:
                (w := w + 1) if result >= 2 else (l := l + 1)
        return w, l

    def h2h_vs_group(t, group):
        w = l = played = 0
        for other in group:
            if other == t:
                continue
            hw, hl = h2h_record(t, other)
            if hw + hl > 0:
                w, l, played = w + hw, l + hl, played + 1
        return w, l, played

    def resolve_group(group):
        if len(group) <= 1:
            return list(group)
        complete = True
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                hw, hl = h2h_record(group[i], group[j])
                if hw + hl == 0:
                    complete = False
        if complete:
            with_h2h = [(t, *h2h_vs_group(t, group)) for t in group]
            with_h2h.sort(key=lambda x: (-(x[1] - x[2]), -tb(x[0])))
            return [x[0] for x in with_h2h]
        for t in group:
            w, l, played = h2h_vs_group(t, group)
            if played == len(group) - 1 and l == 0 and w > 0:
                return [t] + resolve_group([x for x in group if x != t])
        for t in group:
            w, l, played = h2h_vs_group(t, group)
            if played == len(group) - 1 and w == 0 and l > 0:
                return resolve_group([x for x in group if x != t]) + [t]
        return sorted(group, key=lambda t: -tb(t))

    sorted_by_basic = sorted(team_ids, key=lambda t: (-wl(t)[0], wl(t)[1], -rp(t)))
    groups, i = [], 0
    while i < len(sorted_by_basic):
        j = i + 1
        while (j < len(sorted_by_basic)
               and wl(sorted_by_basic[j]) == wl(sorted_by_basic[i])
               and rp(sorted_by_basic[j]) == rp(sorted_by_basic[i])):
            j += 1
        groups.append(sorted_by_basic[i:j])
        i = j

    ordered = [tid for group in groups for tid in resolve_group(group)]
    return {i + 1: id_to_name[tid] for i, tid in enumerate(ordered)}


def _rt_real_winner(conn, season, region, md, team_a, team_b):
    """Winner of a specific real Regional Tournament game (single game for
    MD1, or one leg for two-legged rounds -- md here means one specific
    matchday, e.g. 2 or 3, not the MD2/3 pair)."""
    row = conn.execute("""
        SELECT g.result_a, ta.name a_name, tb.name b_name FROM games g
        JOIN teams ta ON ta.team_id=g.team_a JOIN teams tb ON tb.team_id=g.team_b
        WHERE g.season=? AND g.game_type='P' AND g.host_region=?
        AND g.cup_round=?
        AND ((ta.name=? AND tb.name=?) OR (ta.name=? AND tb.name=?))
    """, (season, region, md, team_a, team_b, team_b, team_a)).fetchone()
    if row is None:
        return None
    won_by_a = row["result_a"] in (2, 3)
    return row["a_name"] if won_by_a else row["b_name"]


def _rt_tie_winner(conn, season, region, md_leg1, md_leg2, team_a, team_b):
    """Combined-score winner of a two-legged tie, or None if either leg
    hasn't been played yet."""
    r1 = conn.execute("""
        SELECT g.pf_a, g.pa_a, g.team_a FROM games g
        JOIN teams ta ON ta.team_id=g.team_a JOIN teams tb ON tb.team_id=g.team_b
        WHERE g.season=? AND g.game_type='P' AND g.host_region=? AND g.cup_round=?
        AND ((ta.name=? AND tb.name=?) OR (ta.name=? AND tb.name=?))
    """, (season, region, md_leg1, team_a, team_b, team_b, team_a)).fetchone()
    r2 = conn.execute("""
        SELECT g.pf_a, g.pa_a, g.team_a FROM games g
        JOIN teams ta ON ta.team_id=g.team_a JOIN teams tb ON tb.team_id=g.team_b
        WHERE g.season=? AND g.game_type='P' AND g.host_region=? AND g.cup_round=?
        AND ((ta.name=? AND tb.name=?) OR (ta.name=? AND tb.name=?))
    """, (season, region, md_leg2, team_a, team_b, team_b, team_a)).fetchone()
    if r1 is None or r2 is None:
        return None
    conn2 = conn
    a_id = conn2.execute("SELECT team_id FROM teams WHERE name=?", (team_a,)).fetchone()["team_id"]
    a_total = 0
    for r in (r1, r2):
        a_total += r["pf_a"] if r["team_a"] == a_id else r["pa_a"]
    b_total = sum(r["pf_a"] if r["team_a"] != a_id else r["pa_a"] for r in (r1, r2))
    return team_a if a_total >= b_total else team_b


def regional_tournament_games(season, region, matchday):
    """[(home,away)] (team names) for a given Regional Tournament matchday
    (1-9), resolved through real results for every prior matchday. Returns
    None if a prior matchday isn't complete yet."""
    seeds = regional_standings_seeds(season, region)
    conn = get_connection()

    def host_pair(seed_a, seed_b):
        # lower (worse, higher number) seed hosts leg1; better hosts leg2 --
        # for MD1 (single game) the better seed always hosts.
        better, worse = (seed_a, seed_b) if seed_a < seed_b else (seed_b, seed_a)
        return seeds[worse], seeds[better]  # (home, away) for leg1 / single game

    # MD1: single games, better seed hosts
    md1_games = []
    for lane in REGIONAL_TOURNAMENT_LANES:
        away_seed, home_seed = lane[0], lane[1]
        md1_games.append((seeds[home_seed], seeds[away_seed]))
    if matchday == 1:
        conn.close()
        return md1_games

    md1_winners = []
    for (home, away), lane in zip(md1_games, REGIONAL_TOURNAMENT_LANES):
        w = _rt_real_winner(conn, season, region, 1, home, away)
        if w is None:
            conn.close()
            return None
        md1_winners.append(w)

    def two_legged_round(incumbents, entrant_seeds, leg1_md, leg2_md, target_md):
        """incumbents: team names advancing in. entrant_seeds: the new
        seed joining each lane this round."""
        leg1_games = []
        for incumbent, lane, entrant_seed in zip(incumbents, REGIONAL_TOURNAMENT_LANES, entrant_seeds):
            entrant = seeds[entrant_seed]
            inc_seed = next(s for s, n in seeds.items() if n == incumbent)
            home, away = host_pair(inc_seed, entrant_seed)
            leg1_games.append((home, away))
        if target_md == leg1_md:
            return leg1_games
        leg2_games = [(away, home) for home, away in leg1_games]
        if target_md == leg2_md:
            return leg2_games
        winners = []
        for (h1, a1) in leg1_games:
            w = _rt_tie_winner(conn, season, region, leg1_md, leg2_md, h1, a1)
            if w is None:
                return None
            winners.append(w)
        return winners

    # MD2/3: MD1 winners vs seeds 8/7/6/5 (lane[2])
    md23_entrants = [lane[2] for lane in REGIONAL_TOURNAMENT_LANES]
    result = two_legged_round(md1_winners, md23_entrants, 2, 3, matchday)
    if matchday in (2, 3):
        conn.close()
        return result
    if result is None:
        conn.close()
        return None
    md23_winners = result

    # MD4/5: MD2/3 winners vs seeds 1/2/3/4 (lane[3])
    md45_entrants = [lane[3] for lane in REGIONAL_TOURNAMENT_LANES]
    result = two_legged_round(md23_winners, md45_entrants, 4, 5, matchday)
    if matchday in (4, 5):
        conn.close()
        return result
    if result is None:
        conn.close()
        return None
    md45_winners = result  # [lane1, lane2, lane3, lane4] survivors

    # MD6/7 semifinal: lane4 vs lane1, lane2 vs lane3
    sf_pairs = [(md45_winners[3], md45_winners[0]), (md45_winners[1], md45_winners[2])]
    sf_leg1 = []
    for team_a, team_b in sf_pairs:
        seed_a = next(s for s, n in seeds.items() if n == team_a)
        seed_b = next(s for s, n in seeds.items() if n == team_b)
        home, away = host_pair(seed_a, seed_b)
        sf_leg1.append((home, away))
    if matchday == 6:
        conn.close()
        return sf_leg1
    if matchday == 7:
        conn.close()
        return [(away, home) for home, away in sf_leg1]

    sf_winners = []
    for (h1, a1) in sf_leg1:
        w = _rt_tie_winner(conn, season, region, 6, 7, h1, a1)
        if w is None:
            conn.close()
            return None
        sf_winners.append(w)

    # MD8/9 final
    team_a, team_b = sf_winners[0], sf_winners[1]
    seed_a = next(s for s, n in seeds.items() if n == team_a)
    seed_b = next(s for s, n in seeds.items() if n == team_b)
    home, away = host_pair(seed_a, seed_b)
    conn.close()
    if matchday == 8:
        return [(home, away)]
    if matchday == 9:
        return [(away, home)]
    raise ValueError(f"Invalid matchday: {matchday}")


# ------------------------------------------------------------- region UI --
# Colors match DECKFIELD's own REGION_COLORS exactly (confirmed from that
# project directly), so the dashboard and the game client are visually
# consistent rather than picking an independent palette.
REGION_COLORS = {
    "Indigo":     {"bright": "#E4574A", "dark": "#7A2A24"},
    "Silver":     {"bright": "#C7CDD6", "dark": "#4A4E56"},
    "Delta":      {"bright": "#F2994A", "dark": "#7A4A1E"},
    "LilyValley": {"bright": "#6FCF97", "dark": "#2E5C3E"},
    "Vertress":   {"bright": "#56CCF2", "dark": "#1E5C6E"},
    "Kalosite":   {"bright": "#B18AF2", "dark": "#4A2E7A"},
    "Lanakila":   {"bright": "#F28CB1", "dark": "#7A2E4E"},
    "Dynamax":    {"bright": "#5B7FD6", "dark": "#1E2E6E"},
    "Phoenix":    {"bright": "#C08A5C", "dark": "#5C3E24"},
    "Terastal":   {"bright": "#E9C46A", "dark": "#6E5A1E"},
}


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def region_colors_with_tint():
    """REGION_COLORS with an added 'tint' (a translucent rgba background
    derived from the dark shade, for badge-style backgrounds)."""
    out = {}
    for region, colors in REGION_COLORS.items():
        r, g, b = _hex_to_rgb(colors["dark"])
        out[region] = {**colors, "tint": f"rgba({r},{g},{b},0.35)"}
    return out


def generate_region_climate():
    """{display_region_name: value(0-1)} -- one fresh random roll per
    region, sqrt(average of three uniform(0,1) draws). This is what
    DECKFIELD itself calls 'Region Climate' -- it's NOT the same as
    on-screen 'Weather' (Perfect/Good/Fair/Bad/Terrible), which DECKFIELD
    derives from this value via its own thresholds; this engine only
    produces the raw 0-1 input. Uses display region names (e.g. 'Lily
    Valley', not 'LilyValley') since DECKFIELD's REGION_COLORS keys --
    and therefore its TEAM_REGION matching -- expect that exact spelling."""
    return {
        region_display_name(region): round(math.sqrt((random.random() + random.random() + random.random()) / 3), 4)
        for region in REGION_COLORS
    }


def export_region_climate_for_deckfield():
    """(dict, tsv_text) -- generate_region_climate()'s output as DECKFIELD's
    expected paste format: tab-separated, 'Region\\tRegion Climate' header
    (confirmed against DECKFIELD's own parseClimatePaste), one row per region."""
    climate = generate_region_climate()
    lines = ["Region\tRegion Climate"] + [f"{region}\t{value}" for region, value in climate.items()]
    return climate, "\n".join(lines)
