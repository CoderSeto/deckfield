#!/usr/bin/env python3
"""
DECKFIELD CLI -- run locally to keep the ratings database updated without
routing every game result through a chat conversation.

Commands:
  deckfield migrate [workbook.xlsm]      Run the full initial migration
  deckfield add-results results.csv      Ingest new game results, recompute ratings
  deckfield recompute [--from N]         Recompute ratings from a given round onward
  deckfield status                       Quick summary of the current database state

CSV format for add-results (header row required, comma-separated):
  round,game_type,team_a,team_b,result_a,pf_a,pa_a,raw_goal_a,raw_goal_b,
  spread_a,interest,dscr_a,dscr_b,elo_a_after,elo_b_after,home[,ex_a,ex_b]

  team_a/team_b are team_id (dex) numbers, not names. result_a is the
  points team_a earned (3 win, 2 OT win, 1 OT loss, 0 loss). home is "A"
  or "B" -- whichever team hosted. ex_a/ex_b are optional, default 0.

Example row:
  12,S,50,113,3,36,33,4.13,3.9,0.93,102,42.7,38.1,2183,2091,A
"""
import argparse
import csv
import sys
from pathlib import Path

import deckfield_ratings as db
import migrate_s9


def cmd_migrate(args):
    migrate_s9.run_full_migration(args.workbook)
    print("\nComputing ratings for all rounds...")
    touched = db.recompute_from_round(args.season, 1)
    print(f"Recomputed rounds: {touched}")
    print("\nMigration complete.")


def cmd_add_results(args):
    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    added, errors = 0, []
    min_round = None

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        missing_cols = set(db.CSV_GAME_FIELDS) - set(reader.fieldnames or [])
        if missing_cols:
            print(f"CSV is missing required columns: {sorted(missing_cols)}", file=sys.stderr)
            sys.exit(1)

        for line_no, row in enumerate(reader, start=2):  # header is line 1
            row = {k: v for k, v in row.items() if v not in (None, "")}
            try:
                result = db.add_game_from_dict(args.season, row)
                added += 1
                if min_round is None or result["round"] < min_round:
                    min_round = result["round"]
            except Exception as e:
                errors.append((line_no, str(e)))

    print(f"Added {added} games.")
    if errors:
        print(f"{len(errors)} rows failed:")
        for line_no, msg in errors:
            print(f"  line {line_no}: {msg}")

    if min_round is not None:
        print(f"Recomputing ratings from round {min_round}...")
        touched = db.recompute_from_round(args.season, min_round)
        print(f"Recomputed rounds: {touched}")
    elif added == 0:
        print("Nothing added -- ratings unchanged.")


def cmd_recompute(args):
    touched = db.recompute_from_round(args.season, args.from_round)
    print(f"Recomputed rounds: {touched}" if touched else "No games found for this season.")


def cmd_status(args):
    conn = db.get_connection()
    n_teams = conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
    n_games = conn.execute(
        "SELECT COUNT(*) c FROM games WHERE season=?", (args.season,)
    ).fetchone()["c"]
    max_round_row = conn.execute(
        "SELECT MAX(round) m FROM games WHERE season=?", (args.season,)
    ).fetchone()
    max_round = max_round_row["m"]
    n_cup_games = conn.execute(
        "SELECT COUNT(*) c FROM games WHERE season=? AND cup_name IS NOT NULL", (args.season,)
    ).fetchone()["c"]
    n_rated_rounds = conn.execute(
        "SELECT COUNT(DISTINCT round) c FROM team_round_ratings WHERE season=?", (args.season,)
    ).fetchone()["c"]
    conn.close()

    print(f"Season {args.season}:")
    print(f"  Teams:            {n_teams}")
    print(f"  Games:            {n_games}" + (f" (through round {max_round})" if max_round else ""))
    print(f"  Tagged cup games: {n_cup_games}")
    print(f"  Rounds rated:     {n_rated_rounds}")


def cmd_next_matchday(args):
    info, csv_text = db.export_matchday_for_deckfield(args.season)
    if info is None:
        print("Everything in the weekly schedule has been played.")
        return
    event = info["event"]
    print(f"Week {info['week']}, {info['day']}: {event}")
    print(f"Use round={info['abs_round']} when reporting results back for this matchday.")
    print()
    print(csv_text)
    if args.output:
        Path(args.output).write_text(csv_text + "\n")
        print(f"\nWritten to {args.output}")


def cmd_export_teams(args):
    teams, tsv = db.export_teams_for_deckfield(args.season)
    print(f"{len(teams)} teams exported.\n")
    print(tsv)
    if args.output:
        Path(args.output).write_text(tsv + "\n")
        print(f"\nWritten to {args.output}")


def cmd_region_climate(args):
    climate, tsv = db.export_region_climate_for_deckfield()
    print(tsv)
    if args.output:
        Path(args.output).write_text(tsv + "\n")
        print(f"\nWritten to {args.output}")


def main():
    parser = argparse.ArgumentParser(prog="deckfield", description="DECKFIELD database CLI")
    parser.add_argument("--season", type=int, default=9, help="Season number (default: 9)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_migrate = sub.add_parser("migrate", help="Run the full initial migration from the Excel workbook")
    p_migrate.add_argument("workbook", nargs="?", default=None,
                            help="Path to the .xlsm workbook (default: bundled path)")
    p_migrate.set_defaults(func=cmd_migrate)

    p_add = sub.add_parser("add-results", help="Ingest new game results from a CSV file and recompute ratings")
    p_add.add_argument("csv_file", help="Path to a CSV file with a header row (see module docstring for format)")
    p_add.set_defaults(func=cmd_add_results)

    p_recompute = sub.add_parser("recompute", help="Recompute ratings from a given round onward")
    p_recompute.add_argument("--from", dest="from_round", type=int, default=1,
                              help="Round to start recomputing from (default: 1)")
    p_recompute.set_defaults(func=cmd_recompute)

    p_status = sub.add_parser("status", help="Show a quick summary of the current database state")
    p_status.set_defaults(func=cmd_status)

    p_next = sub.add_parser("next-matchday", help="Export the next unplayed matchday in DECKFIELD's Schedule paste format")
    p_next.add_argument("--output", "-o", help="Write the CSV to this file instead of just printing it")
    p_next.set_defaults(func=cmd_next_matchday)

    p_teams = sub.add_parser("export-teams", help="Export the full team roster in DECKFIELD's Teams paste format")
    p_teams.add_argument("--output", "-o", help="Write the TSV to this file instead of just printing it")
    p_teams.set_defaults(func=cmd_export_teams)

    p_climate = sub.add_parser("region-climate", help="Roll and export fresh Region Climate values in DECKFIELD's paste format")
    p_climate.add_argument("--output", "-o", help="Write the TSV to this file instead of just printing it")
    p_climate.set_defaults(func=cmd_region_climate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
