"""
DECKFIELD database -- setup and round-by-round updates.

Run this file directly for a working demo:
    python3 deckfield_db.py

Everything lives in one SQLite file (deckfield.db), created next to this
script the first time it runs. No server, no install -- sqlite3 ships
with Python.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "deckfield.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the schema. Safe to run more than once -- won't wipe existing data."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            team_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            region TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            round INTEGER NOT NULL,
            home_team_id INTEGER NOT NULL REFERENCES teams(team_id),
            away_team_id INTEGER NOT NULL REFERENCES teams(team_id),
            home_score INTEGER,
            away_score INTEGER
        );
    """)
    conn.commit()
    conn.close()


def add_teams(teams):
    """teams: list of (name, region) tuples. Existing names are skipped, not duplicated."""
    conn = get_connection()
    conn.executemany(
        "INSERT OR IGNORE INTO teams (name, region) VALUES (?, ?)", teams
    )
    conn.commit()
    conn.close()


def _team_id(conn, name):
    row = conn.execute("SELECT team_id FROM teams WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"No team named {name!r} -- add it with add_teams() first.")
    return row["team_id"]


def add_round(round_num, results):
    """
    Add one completed round of games.
    results: list of (home_name, away_name, home_score, away_score) tuples.
    This is the only thing you run each week -- standings below update themselves.
    """
    conn = get_connection()
    for home, away, hs, away_score in results:
        conn.execute(
            """INSERT INTO games (round, home_team_id, away_team_id, home_score, away_score)
               VALUES (?, ?, ?, ?, ?)""",
            (round_num, _team_id(conn, home), _team_id(conn, away), hs, away_score),
        )
    conn.commit()
    conn.close()


def get_standings():
    """Wins/losses/points, recomputed live from every game played so far.
    Nothing is cached, so there's no stale-numbers risk -- it's always
    a fresh answer as of whatever rounds have been added."""
    conn = get_connection()
    rows = conn.execute("""
        WITH results AS (
            SELECT home_team_id AS team_id, away_team_id AS opp_id,
                   home_score AS pf, away_score AS pa
            FROM games WHERE home_score IS NOT NULL
            UNION ALL
            SELECT away_team_id, home_team_id, away_score, home_score
            FROM games WHERE home_score IS NOT NULL
        )
        SELECT t.name, t.region,
               COUNT(*) AS games_played,
               SUM(CASE WHEN r.pf > r.pa THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN r.pf < r.pa THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN r.pf = r.pa THEN 1 ELSE 0 END) AS ties,
               SUM(r.pf) AS points_for,
               SUM(r.pa) AS points_against
        FROM results r
        JOIN teams t ON t.team_id = r.team_id
        GROUP BY t.team_id
        ORDER BY wins DESC, (SUM(r.pf) - SUM(r.pa)) DESC
    """).fetchall()
    conn.close()
    return rows


if __name__ == "__main__":
    # One-time setup: create the schema and register the teams.
    init_db()
    add_teams([
        ("Ironclad Foxes", "North"),
        ("Salt Flat Wardens", "West"),
        ("Redline Marauders", "South"),
        ("Driftwood Cellars", "East"),
    ])

    # Each week: just call add_round() with that round's results.
    add_round(1, [
        ("Ironclad Foxes", "Driftwood Cellars", 24, 10),
        ("Salt Flat Wardens", "Redline Marauders", 17, 20),
    ])
    add_round(2, [
        ("Ironclad Foxes", "Redline Marauders", 14, 14),
        ("Salt Flat Wardens", "Driftwood Cellars", 30, 6),
    ])

    # Pulling stats is always live -- no manual recompute step.
    print(f"{'Team':<20} {'Region':<8} {'GP':<4} {'W':<3} {'L':<3} {'T':<3} {'PF':<5} {'PA':<5}")
    for row in get_standings():
        print(f"{row['name']:<20} {row['region']:<8} {row['games_played']:<4} "
              f"{row['wins']:<3} {row['losses']:<3} {row['ties']:<3} "
              f"{row['points_for']:<5} {row['points_against']:<5}")
