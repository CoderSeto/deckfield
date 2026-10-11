"""Extract a finished pre-S9 season from its workbook into history/s<N>/.

    python3 extract_history.py 8 "history/s8/Baccer Game S8 Stats.xlsm"
    python3 extract_history.py 3 history/s1-3/Dice_Game_S123.xlsx    # S1-S3 share it

Seasons 1-3 are read by history_early.py (one workbook for all three, their
Elo in history/s1-3/DG_MRLUCL_Experimenting.xlsx); the checks are these.

The historical archive keeps exactly what the season RECORDED. Nothing here is
simulated, estimated or recomputed with today's formulas: a stat the workbook
never recorded stays blank. Anything filled in later ("relitigating") belongs
in a separate layer beside these files, never in them.

What it writes, all under history/s<N>/:

- games.csv      one row per game, Archive order. team_a is the HOST (home is
                 "A", as in the modern CSV); where no host is known (some S1/S3
                 games) team_a is the winner and home is blank. host_source says
                 where the host came from: "recorded" (the sheet's H/A), or for
                 S1-S2 "schedule", "interc", "record" or "seed" (rebuilt under
                 the user's rules -- see history_early.py). Team ids are CANONICAL --
                 the team's S9 dex -- not the season's own dex, which is not a
                 stable identity (see history/teams.json).
                 rank_a/rank_b are each side's rank GOING INTO the game (Archive
                 H/M). goals_a/goals_b are goals scored (Archive N, written
                 [winner-loser]); elo_k is the game's Elo K (Archive E): the
                 points the winner gained and the loser lost, exactly.
                 raw_winner/raw_loser
                 are the two packed game strings verbatim, for audit.
- walkovers.csv  byes the season scored as wins (S8: the Grand Swiss
                 round-1/round-2 byes, from Calcs' "3S-" cells).
- teams.csv      each team as it stood that season: its own dex, canonical id,
                 name, region, division, opening rank and Elo (where it stood
                 coming in), final rank / OVR / Elo, accolades.
- events.json    every event in play order with its game count (an event with
                 no games, like a leg 3 nobody had to play, is listed and
                 marked empty), its play dates where the sheet kept them (also per
                 game, as games.csv's `day`), the
                 changelog notes the sheet wrote beside it, any listed
                 anomalies, and the source workbook's SHA-256.

It also adds the season to history/teams.json, the cross-season identity map.

It refuses to write unless every check against the workbook's own totals and
against later records passes -- see validate(). A recorded inconsistency the
season really contains is accepted only when it is listed, row by row with its
reason, in that season's "anomalies"; anything unlisted still refuses. A
recorded value the user has ruled wrong is replaced only through that season's
"corrections" (Archive row, field, the sheet's value, the corrected value, the
reason), each written to events.json; the sheet's value must still be what the
entry says, so a correction cannot silently go stale.
Season-specific layout lives in SEASONS.
"""
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

import openpyxl

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(ROOT, "history")

GOALS_PATTERN = re.compile(r'^\[(\d+)-(\d+)\]$')  # Archive N: [winner goals-loser goals]
GAME_PATTERN = re.compile(
    r'^(\d)([RLSPFW])!(-?\d+)\+(-?\d+)-(-?\d+)\^(-?[\d.]+)\((-?[\d.]+)\)'
    r'\[(-?[\d.]+)\]\{(-?[\d.]+)\}(-?[\d.]+)$'
)
# S5's early games carry only the result and type ("3R", "0R"; one cell reads
# "OR", a letter O for the zero). Everything else is in the row's own columns.
SHORT_PATTERN = re.compile(r'^([0-3O])([RLSPFW])$')
DSCR_PAIR_PATTERN = re.compile(r'^\{(-?[\d.]+)-(-?[\d.]+)\}$')
WALKOVER_PATTERN = re.compile(r'^(\d)([RLSPFW])-$')
# S4's Calcs strings open with the side and the result instead of result and
# game type: "AW!68+32-9^7(13.97)[60]{90.5}2153" = away, won, v dex 68, 32-9.
CALCS_S4_PATTERN = re.compile(
    r'^([HA])([WL])!(-?\d+)\+(-?\d+)-(-?\d+)\^(-?[\d.]+)\((-?[\d.]+)\)'
    r'\[(-?[\d.]+)\]\{(-?[\d.]+)\}(-?[\d.]+)$'
)
EX_PATTERN = re.compile(r'^/(-?\d+)/$')

LEAGUE_DIVISION_MAP = {
    "First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5,
    "Sixth": 6, "Seventh": 7, "Eighth": 8, "Ninth": 9, "Tenth": 10,
}
REGION_NAME_FIX = {"Lily Valley": "LilyValley"}
# S6's pyramid: one Master division, two Ultra, three Great, four Basic.
# S4's leagues were the MRL (Master) and UTL (Ultra), each in lettered groups.
LEAGUE_TIER = {"Master": 1, "Ultra": 2, "Great": 3, "Basic": 4, "MRL": 1, "UTL": 2}
NUMBER_WORDS = {w: i for i, w in enumerate(
    "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN "
    "FOURTEEN FIFTEEN".split(), start=1)}
ORDINAL_WORDS = {w: i for i, w in enumerate("FIRST SECOND THIRD FOURTH FIFTH".split(), start=1)}

# A team renamed since that season, old name -> its S9 name. Each entry is
# proved by S9's own "S8 End" rank checkpoint, which lists the team under its
# S9 name with the rank it finished S8 on (validate() checks all 160).
RENAMED_SINCE = {
    "Resort Area": "Battle Zone",
    "Fight Area": "Cabo Poco",
    # S4 -> S5, same dex (Dynamax); proved by S5's "S4 End". The Hall of Fame
    # lists S4's MRL runner-up as "Wild Area*".
    "South Lakes": "Wild Area",
    # S5 -> S6, same dex and region (Terastal); proved by S6's "S5 End".
    "South Kalos": "Blueberry Terarium",
    "East Paldea": "Mossui Town",
}

# The Archive's columns (0-based) from S6 to S8. S5 has no Game ID column, so
# everything from the spread on sits one place to the left.
ARCHIVE_COLS_S6_S8 = dict(date=0, label=1, spread=3, k=4, interest=5, ha=6, rank_w=7,
                          winner=8, score_w=9, score_l=10, loser=11, rank_l=12, goals=13,
                          dscr=14, w=15, l=16)
DEFAULT_BUCKETS = {"R": "R", "L": "L", "S": "S", "P": "p", "F": "p"}

# Rankings columns are 0-based. Common to S7 and S8: A dex, B region, C name,
# F..M the W-L the workbook kept per bucket, AQ OVR, BL Elo, BQ base climate,
# BV division, CG the name keying the rank-checkpoint block (checked equal to C).
SEASONS = {
    8: {
        # BY primary type, CA accolades, CK the FINAL rank checkpoint, EU
        # "S7 End", FV the Elo carried in from S7 (checked against the Elo the
        # games imply before each team's first game: equal for all 160).
        "rankings": dict(dex=0, region=1, name=2, ovr=42, elo=63, basclm=68,
                         division=73, primary=76, accolades=[78], checkpoint_name=84,
                         final_rank=88, opening_rank=150, opening_elo=177),
        "record_cols": dict(RW=5, RL=6, LW=7, LL=8, SW=9, SL=10, pW=11, pL=12),
        # Archive columns (0-based) after the packed strings: R the MAX/INVT
        # mark, S the winner's EX, T the notes.
        "archive_cols": dict(ARCHIVE_COLS_S6_S8, mark=17, ex=18, note=19),
        # Calcs: per-team game grid, column L onward in play order.
        "calcs_first_col": 12,
        "anomalies": {
            3122: ("interest", "Regional matchday 14: the interest column reads 6 more "
                               "than the game string (82 v 76). The string's value is archived."),
            3123: ("interest", "Regional matchday 14: the interest column reads 6 more "
                               "than the game string (85 v 79). The string's value is archived."),
            3124: ("interest", "Regional matchday 14: the interest column reads 6 more "
                               "than the game string (83 v 77). The string's value is archived."),
            3125: ("interest", "Regional matchday 14: the interest column reads 6 more "
                               "than the game string (87 v 81). The string's value is archived."),
            3126: ("interest", "Regional matchday 14: the interest column reads 6 more "
                               "than the game string (85 v 79). The string's value is archived."),
            3127: ("interest", "Regional matchday 14: the interest column reads 6 more "
                               "than the game string (88 v 82). The string's value is archived."),
        },
        "corrections": {},
        "cup": ("grand_swiss", "two_leg_final"),
    },
    7: {
        # No primary type column. Accolades in CA and CB. CK the final rank
        # checkpoint, ER "S6 End". Its carried-in Elo block (FR..GN, headed
        # "NOT FIXED") disagrees with the games for all 160 teams and is not
        # read; opening Elo comes from the games alone.
        "rankings": dict(dex=0, region=1, name=2, ovr=42, elo=63, basclm=68,
                         division=73, primary=None, accolades=[78, 79], checkpoint_name=84,
                         final_rank=88, opening_rank=147, opening_elo=None),
        "record_cols": dict(RW=5, RL=6, LW=7, LL=8, SW=9, SL=10, pW=11, pL=12),
        "archive_cols": dict(ARCHIVE_COLS_S6_S8, mark=17, ex=18, note=19),
        "calcs_first_col": 12,
        "anomalies": {
            2591: ("spread", "Grand Swiss R16 leg 1: the spread column reads -3.584 while the "
                             "game string says -4.08. The string's value is archived."),
        },
        "corrections": {
            3222: ("elo_k", 22, 40,
                   "Delta playoff SF leg 1, Fallarbor Town (57) won at Lavaridge Town "
                   "(25): Elo moved 40 each way (1879->1919, 2041->2001) but the K "
                   "column says 22. Per the user (2026-10-11): whatever the Elo "
                   "actually changed by stays, so K is 40."),
        },
        "cup": ("grand_swiss", "two_leg_final"),
    },
    6: {
        # The league is a pyramid: BV the tier (Master/Ultra/Great/Basic), BW
        # the division letter within it (blank for Master). EP "S5 End". The
        # carried-in Elo block (ES..FO) is the same stale copy S7 has; not
        # read. No primary type.
        "rankings": dict(dex=0, region=1, name=2, ovr=42, elo=63, basclm=68,
                         division=73, division_letter=74, primary=None, accolades=[78, 79],
                         checkpoint_name=84, final_rank=88, opening_rank=145, opening_elo=None),
        "record_cols": dict(RW=5, RL=6, LW=7, LL=8, SW=9, SL=10, pW=11, pL=12),
        # No per-game EX column: the notes moved into S.
        "archive_cols": dict(ARCHIVE_COLS_S6_S8, mark=17, ex=None, note=18),
        "calcs_first_col": 12,
        "anomalies": {
            3341: ("interest", "Regional playoff SF leg 2: interest column 92, game string 90. "
                               "The string's value is archived."),
            3443: ("interest", "Championship Swiss round 3 leg 2: interest column 103, game "
                               "string 101. The string's value is archived."),
            3496: ("interest", "Championship R16 leg 2: interest column 101, game string 99. "
                               "The string's value is archived."),
        },
        "corrections": {},
        # A changelog note written into a round label's cell.
        "label_overrides": {
            3516: ("Semifinal - Game Three", "formula 12.3>12.4"),
        },
        "cup": ("grand_swiss", "two_leg_final"),
    },
    4: {
        # 128 teams in 8 regions; no league divisions. A team's "division" is its
        # MRL/UTL group (BT/BU), blank for the 80 teams in neither. CA keys the
        # rank checkpoints, CB "W25 F" is the final rank, DN "Start" the opening.
        # Accolades BW/BX. No base climate or primary type column.
        "rankings": dict(dex=0, region=1, name=2, ovr=42, elo=63, basclm=None,
                         division=71, division_letter=72, primary=None, accolades=[74, 75],
                         checkpoint_name=78, final_rank=79, opening_rank=117, opening_elo=None),
        # Phoenix's 16 teams were new; their starting Elo came from an unofficial
        # preseason mini tournament (the ...X tabs, excluded from everything) and
        # sits in CalcsX column H.
        "opening_elo_sheet": ("CalcsX", 3, 18, 0, 7),
        # Records: Se = the 12 weekend matchday games (9 against the team's own
        # region = Cf, 3 interconference pod games), Ex = everything else. Cf is
        # inside Se, not beside it. Proved for all 128: Se + Ex = games played.
        "record_cols": dict(SeW=5, SeL=6, CfW=8, CfL=9, ExW=11, ExL=12),
        "record_buckets": {"R": ("Se", "Cf"), "I": ("Se",), "M": ("Ex",), "W": ("Ex",),
                           "P": ("Ex",), "F": ("Ex",)},
        # The Archive holds no game strings at all; every game's strings are in
        # Calcs, in S4's own format (side + result, no game type).
        "archive_cols": dict(date=0, label=1, spread=2, k=3, interest=4, ha=5, rank_w=6,
                             winner=7, score_w=8, score_l=9, loser=10, rank_l=11, goals=12,
                             dscr=13, w=None, l=None, mark=14, ex=None, note=15),
        "calcs_grid": (11, 93),
        "calcs_first_col": 12,
        "calcs_pattern": "s4",
        "fill_short_from_calcs": True,
        "caret_is_goals": True,
        # The strings carry no game type, so it comes from the event. A weekend
        # matchday game is R (same region) or I (interconference pod).
        "type_by_code": [(r'^MD\d+$', "R/I"), (r'^(MRL|UTL)-', "M"), (r'^WCS-', "W"),
                         (r'^CC-', "P"), (r'^(PO-|WC-F)', "F")],
        "mixed_types": {"R", "I"},
        "round_robins": [],
        "extra_titles": re.compile(r'^(Matchday \w+ \((Sat|Sun)\)|(UTL|MRL|WCS) .+|'
                                   r'(\w+ )*Conference Championship|PLAYOFFS - .+|'
                                   r'World Championship - .+)$'),
        "label_overrides": {
            282: ("UTL Matchday B (Tue)", "First automated game without cards"),
            283: (None, None),
            1107: (None, "RIVALRY WEEK"),
        },
        "cup": ("wcs", "two_leg_final", ["WCS-F-1", "WCS-F-2"]),
        "regional_final": ["CC-F"],
        # The Hall of Fame's S4 league plaque: MRL champion and second, UTL
        # champion and second (labelled "UTL Champion A/B" in the sheet).
        "league_finals": [["MRL-F-1", "MRL-F-2"], ["UTL-F-1", "UTL-F-2"]],
        "anomalies": {
            "Tilt Village: opening Elo": (
                "opening", "CalcsX column H gives Tilt Village (11th in the Phoenix "
                "preseason) 1777; its S4 games start from 1797, which is the preseason "
                "pattern (1808 down to 1792 by finish) and makes the league's opening Elo "
                "total exactly 230,400 (1800 x 128). The games' 1797 is archived."),
        },
        "corrections": {},
        # Archive cells corrected as the row is read (row, column, the sheet's
        # value, the corrected value, the reason).
        "cell_corrections": {
            500: ("winner", "a", "Littleroot Town",
                  "WCS Round 2-B, the season's first 5OT game: the winner cell reads "
                  "\"a\". Calcs has it: Littleroot Town beat Snowbelle City 25-23 at home "
                  "(Snowbelle's 7-R string, AL!18+23-25, dex 18 = Littleroot Town)."),
        },
        # Ties decided in aggregate overtime. The Archive (like S5's strings) has
        # the leg's regulation score; Calcs folds the aggregate-OT points into it
        # (Solaceon Town v Virbank City reads 22-22, still Solaceon's win). The
        # regulation score is archived and the AggOT points go in aggot_a/b.
        "aggot_rows": {
            1088: "UTL R16 leg 2: 18-15 in regulation, 26-26 on aggregate; Anville Town "
                  "advances 32-26 (Calcs 24-15).",
            1207: "WCS QF leg 2: 21-12 to Solaceon Town in regulation, 32-32 on aggregate; "
                  "Virbank City advances 42-33 (Calcs 22-22).",
            1310: "Playoffs QF leg 2: 19-10 to Castelia City in regulation, 34-34 on "
                  "aggregate; Spikemuth advances 43-41 (Calcs 26-19).",
        },
    },
    5: {
        # Records are kept in three buckets: Conference (Regional) CW/CL, League
        # LW/LL, and "Ex" ExW/ExL for everything else (WCS, playoffs, Finals).
        # OVR ("TOTAL") is on a 0-1 scale. EN "S4 End". No primary type.
        "rankings": dict(dex=0, region=1, name=2, ovr=42, elo=63, basclm=68,
                         division=73, division_letter=74, primary=None, accolades=[78, 79],
                         checkpoint_name=84, final_rank=88, opening_rank=143, opening_elo=None),
        "record_cols": dict(CW=5, CL=6, LW=8, LL=9, ExW=11, ExL=12),
        "record_buckets": {"R": "C", "L": "L", "W": "Ex", "P": "Ex", "F": "Ex"},
        "archive_cols": dict(date=0, label=1, spread=2, k=3, interest=4, ha=5, rank_w=6,
                             winner=7, score_w=8, score_l=9, loser=10, rank_l=11, goals=12,
                             dscr=13, w=14, l=15, mark=16, ex=None, note=17),
        "calcs_first_col": 12,
        # The game string's "^" field is GOALS this season (equal to column M in
        # all 2,435 full strings), not the modern raw goal score.
        "caret_is_goals": True,
        # Elo was applied out of Archive order (e.g. the second WCS First Round
        # Nature block reached teams' Elo only after Regional matchday 5), so the
        # game-by-game chain does not hold. The recorded Elo is kept; the
        # opening/final total and final-Elo checks still run.
        "elo_chain": False,
        # The Archive's short-form games ("3R") are completed from Calcs, the
        # per-team game grid, which holds every game's full string. Calcs is
        # also what Rankings' records were computed from -- and its R11 column
        # is misfiled (160 correct strings, about half in the wrong team's row),
        # which is why Rankings' Regional W-L disagrees with the Archive for 26
        # teams. So S5's records are checked as: Rankings == Calcs' records
        # exactly, and every Archive game == its Calcs strings (found in the
        # teams' own rows, or anywhere in a misfiled column).
        "calcs_grid": (11, 96),
        "fill_short_from_calcs": True,
        "records_from_calcs": True,
        "calcs_misfiled": {"R11": "Calcs' R11 column has the 160 right strings, about half "
                                  "of them in the wrong team's row"},
        "extra_titles": re.compile(r'^(Matchday \w+|WCS .+|CHAMPIONSHIP - Game \w+)$'),
        "event_code_overrides": {"Matchday One": "R1", "Matchday Two": "R2",
                                 "Matchday Three": "R3", "Matchday Six": "R4"},
        # Promotion-playoff games written one row at a time among other events.
        "inline_events": re.compile(r'^MGGG Playoff'),
        "label_overrides": {
            451: (None, "GREAT RESET"),
            690: ("LEAGUE MATCHDAY THREE", "Special Week Two"),
            691: (None, None),
            3041: ("Semifinal - Game Two", None),
        },
        "cup": ("wcs", "wcs"),
        "anomalies": {
            2898: ("interest", "Regional SF leg 2, Wild Area v Spikemuth, decided in "
                               "aggregate OT: the interest column reads 99.15 while the "
                               "game string's interest is 95. Both kept as written."),
            1750: ("calcs", "WCS fifth round (Justice): Calcs gives the winner's Elo as "
                            "2352, the Archive 2349. The Archive's is archived."),
            1751: ("calcs", "WCS fifth round (Justice): Calcs gives the winner's Elo as "
                            "2144, the Archive 2128. The Archive's is archived."),
            1752: ("calcs", "WCS fifth round (Justice): Calcs gives the winner's Elo as "
                            "2280, the Archive 2267. The Archive's is archived."),
            1775: ("calcs", "Regional matchday 10: Calcs gives the winner's Elo as 2282, "
                            "the Archive 2269. The Archive's is archived."),
            1778: ("calcs", "Regional matchday 10: Calcs gives the winner's Elo as 2353, "
                            "the Archive 2350. The Archive's is archived."),
            1822: ("calcs", "Regional matchday 10: Calcs gives the loser's Elo as 2123, "
                            "the Archive 2107. The Archive's is archived."),
        },
        "corrections": {},
    },
}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

# Seasons 1-3 live in one workbook (history/s1-3/Dice_Game_S123.xlsx) and
# their Elo in another (DG_MRLUCL_Experimenting.xlsx); history_early.py reads
# them. No game strings survive: each game is its row's columns, its game type
# its event's, its Elo the weekly Elo sheet's.
EARLY_ELO_WORKBOOK = "history/s1-3/DG_MRLUCL_Experimenting.xlsx"
EARLY_TYPES = [(r'^MD\d+$', "R/I"), (r'^WCS-', "W"), (r'^(CRY|EME|PLA|GRA|PRI|KAH|GIG)-', "C"),
               (r'^(MRL|UTL)-', "M"), (r'^CC-', "P"), (r'^WC-', "F")]


def _weeks(n_regular, post):
    """S1/S2's events are the Elo workbook's weeks: W1..Wn the regular season,
    then the postseason in order."""
    out = {f"W{i}": (f"MD{i}", f"Week {i}") for i in range(1, n_regular + 1)}
    for i, ev in enumerate(post, start=n_regular + 1):
        out[f"W{i}"] = ev
    return out


SEASONS.update({
    3: {
        # 112 teams in 7 conferences (Dynamax new). Rankings: A dex, CD the
        # FINAL rank checkpoint (keyed by CC's name), DG "Preseason" (S2's
        # final order, with the 16 Dynamax teams ranked in), BC TOTAL.
        "reader": "early",
        "rankings": dict(sheet="Rankings", dex=0, region=1, division=2, name=3, ovr=54,
                         final_rank=82, opening_rank=110, checkpoint_name=81),
        "elo_workbook": EARLY_ELO_WORKBOOK, "elo_sheet": "Season 3",
        "elo_archive": dict(sheet="Archive", week=1, winner=7, score_w=8, score_l=9, loser=10, goals=12),
        # Records as S4's: Se = the 13 matchday games, Cf = those within the
        # conference (inside Se), Ex = everything else.
        "record_cols": dict(SeW=5, SeL=6, CfW=8, CfL=9, ExW=11, ExL=12),
        "record_buckets": {"R": ("Se", "Cf"), "I": ("Se",), "C": ("Ex",), "W": ("Ex",),
                           "M": ("Ex",), "P": ("Se",), "F": ("Ex",)},
        "archive": "Archive",
        "archive_cols": dict(date=0, label=1, spread=2, interest=3, ha=4, rank_w=5, winner=6,
                             score_w=7, score_l=8, loser=9, rank_l=10, goals=11, dscr=12,
                             mark=13, note=14),
        # A changelog note written in the label cell of a game that opens the
        # next event.
        "label_overrides": {
            742: ("UTL Group A - Match 2 - 1st Leg", "New Rating Formula Instituted"),
        },
        "type_by_code": EARLY_TYPES,
        "mixed_types": {"R", "I"},
        "round_robins": [],
        # 252 early games (matchdays 1, 2, 4 and 5, cup round 1) recorded no host.
        "host_optional": True,
        "next_rank": "order",
        "cup": ("wcs", "two_leg_final", ["WCS-F-1", "WCS-F-2"]),
        "regional_final": ["CC-F"],
        "league_finals": [["MRL-F-1", "MRL-F-2"], ["UTL-F-1", "UTL-F-2"]],
        # The World Championship was double elimination; the champion won the
        # Grand Final reset, 2-1.
        "world_final": ["WC-GFR-1", "WC-GFR-2", "WC-GFR-3"],
        # Ties decided in aggregate overtime (the numbers are read from each
        # row's note and checked against the legs).
        "aggot_rows": {
            461: "WCS round 3 leg 2: Cyllage City won the leg, Ever Grande City the tie.",
            725: "WCS round 4 leg 2.",
            954: "WCS round 5 leg 2: Santalune City won the leg, Viridian City the tie.",
            1267: "WCS semifinal leg 2: South Lakes won the leg, Kala'e Bay the tie.",
            1309: "World Championship Lower First Round leg 2.",
            1328: "World Championship Lower Final leg 2.",
        },
        # Rankings' own count is one off for three teams, each time on one side
        # of a game only (the opponent's record in it reconciles), and every
        # game is confirmed by the weekly Elo: both teams' Elo moved that week.
        "anomalies": {
            "Cinnabar Island: ExW": ("records", "Rankings counts 19 non-matchday wins; the "
                                                "games hold 20. The games are archived."),
            "Celestic Town: ExL": ("records", "Rankings counts 5 non-matchday losses; the "
                                              "games hold 6. The games are archived."),
            "Solaceon Town: ExW": ("records", "Rankings counts 8 non-matchday wins; the "
                                              "games hold 9. The games are archived."),
        },
        "corrections": {},
        "cell_corrections": {
            177: ("dscr", "{42.2-4.8)", "{42.2-4.8}",
                  "Matchday 4, Hau'oli City over Heahea City: the DSCR pair's closing brace "
                  "is typed as a parenthesis."),
        },
    },
    2: {
        # 96 teams in 6 conferences (Lanakila new), 12 divisions of 8.
        # S2 Rankings: A the team's code, E its playoff seed, Y its final rank,
        # AA TOTAL.
        "reader": "early",
        "rankings": dict(sheet="S2 Rankings", dex=0, region=1, division=2, name=3, ovr=26,
                         final_rank=24, seed=4),
        "elo_workbook": EARLY_ELO_WORKBOOK, "elo_sheet": "Season 2",
        "elo_archive": dict(sheet="S2 Archive", week=1, winner=4, score_w=5, score_l=6, loser=7, goals=9),
        # Wins/Losses = every game; CF = regular-season conference games only.
        "record_cols": dict(W=5, L=6, CfW=8, CfL=9),
        "record_buckets": {"R": ("", "Cf"), "I": ("",), "P": ("",), "F": ("",)},
        "archive": "S2 Archive",
        "archive_cols": dict(label=2, rank_w=3, winner=4, score_w=5, score_l=6, loser=7,
                             rank_l=8, goals=9, note=10),
        "week_events": _weeks(12, [
            ("CC-F", "Conference Championships"),
            ("WC-PI-1", "Playoffs - Play-in, First Leg"), ("WC-PI-2", "Playoffs - Play-in, Second Leg"),
            ("WC-QF-1", "Playoffs - Quarterfinal, First Leg"), ("WC-QF-2", "Playoffs - Quarterfinal, Second Leg"),
            ("WC-SF-1", "Playoffs - Semifinal, First Leg"), ("WC-SF-2", "Playoffs - Semifinal, Second Leg"),
            ("WC-F-1", "Playoffs - Final, Game One"), ("WC-F-2", "Playoffs - Final, Game Two")]),
        "type_by_code": EARLY_TYPES,
        "mixed_types": {"R", "I"},
        "round_robins": [],
        "division_rr": True,
        # Hosts, per the user (2026-10-11): S2 Divisions' template, the second
        # team of "x=y" hosting; crossovers its B rounds, interconference its
        # round 4 ("4-X"); the better record hosts a conference championship.
        # A playoff tie's first leg is hosted by the worse seed and its second
        # by the better seed, as now.
        "schedule": dict(positions="s2", orientation="second", crossover="B",
                         regular_weeks={f"W{i}" for i in range(1, 13)}),
        "host_declarations": [("Verdanturf Town", "Rustboro City")],
        "next_rank": "order",
        "cup": None,
        "regional_final": ["CC-F"],
        "world_final": ["WC-F-1", "WC-F-2"],
        "aggot_rows": {
            594: "Playoff quarterfinal leg 2.",
        },
        "anomalies": {
            "Ambrette Town: CfL": ("records", "Rankings gives a 3-7 conference record, ten "
                                              "games; Ambrette Town played nine (3-6), every one "
                                              "confirmed by the weekly Elo. The games are archived."),
        },
        "corrections": {},
    },
    1: {
        # 80 teams in 5 conferences, 10 divisions of 8. S1 Rankings: A the
        # team's code, E its World Championship seed, U its final rank, V TOTAL.
        "reader": "early",
        "rankings": dict(sheet="S1 Rankings", dex=0, region=1, division=2, name=3, ovr=21,
                         final_rank=20, seed=4),
        "elo_workbook": EARLY_ELO_WORKBOOK, "elo_sheet": "Season 1",
        "elo_archive": dict(sheet="S1 Archive", week=0, winner=3, score_w=4, score_l=5, loser=6, goals=7),
        # Wins/Losses = every game; CF = regular-season conference games only.
        "record_cols": dict(W=5, L=6, CfW=8, CfL=9),
        "record_buckets": {"R": ("", "Cf"), "I": ("",), "P": ("",), "F": ("",)},
        "archive": "S1 Archive",
        "archive_cols": dict(date=1, label=2, winner=3, score_w=4, score_l=5, loser=6,
                             goals=7, note=8),
        # Column C holds poll ranks ("#25 v. #15", winner first) through the
        # conference championships; the World Championship's are seeds.
        "label_ranks": {f"W{i}" for i in range(1, 15)},
        "week_events": _weeks(13, [
            ("CC-F", "Conference Championships"),
            ("WC-G1", "World Championship - Group Stage, Opening Games"),
            ("WC-G2", "World Championship - Group Stage, Winners and Losers"),
            ("WC-G3", "World Championship - Group Stage, Deciders"),
            ("WC-SF", "World Championship - Semifinals"),
            ("WC-F", "World Championship - Final")]),
        "type_by_code": EARLY_TYPES,
        "mixed_types": {"R", "I"},
        "round_robins": [],
        "division_rr": True,
        # Hosts, per the user (2026-10-11): the template reversed (the FIRST
        # team of "x=y" hosts) on S1's own positions; crossovers its A rounds;
        # interconference from S1 InterC (the listed team at home in weeks 1
        # and 7, away in 2 and 11 -- reading confirmed by the user); the World
        # Championship's single games go to the higher seed.
        "schedule": dict(positions="s1", orientation="first", crossover="A",
                         regular_weeks={f"W{i}" for i in range(1, 14)},
                         interc=("S1 InterC", 3, 42, 2,
                                 {11: (1, "away"), 1: (4, "home"), 2: (5, "away"), 7: (6, "home")})),
        "host_declarations": [("Rustboro City", "Verdanturf Town")],
        # Two conference championships were between teams with the same
        # regular-season record (overall and in conference) who never met, so
        # "the better record hosts" does not decide them; their hosts stay
        # blank until a tiebreak is ruled.
        "host_optional": {524: "Lily Valley championship, Celestic Town v Sandgem Town (both 11-2, 8-1)",
                          525: "Kalosite championship, Vaniville Town v Cyllage City (both 10-3, 8-1)"},
        "next_rank": None,
        "cup": None,
        "regional_final": ["CC-F"],
        "world_final": ["WC-F"],
        "anomalies": {
            228: ("more", "Week 6, Shalour City 12-8 over Geosenge Town: goals read [8-9], "
                           "nine goals for a side that scored eight points. Kept as recorded."),
        },
        "corrections": {},
    },
})


STRUCTURAL = re.compile(r'\b(Leg|Round|Game|Quarterfinal|Semifinal|Championship|Play-in|'
                        r'Nature|Justice)\b', re.I)


def _is_title(label, cfg=None):
    """An event title opens with two ALL-CAPS words ("GRAND SWISS FINAL - First
    Leg"); anything else refines the current event ("First Leg") or annotates
    it ("The Overhaul", and S7's "EYE formula fixed", whose first word alone
    is capitals)."""
    extra = (cfg or {}).get("extra_titles")
    if extra is not None and extra.match(label):
        return True
    # Tokens without a letter ("-") are skipped: "FINALS - MASTERS TOURNAMENT".
    words = [w for w in label.split() if any(ch.isalpha() for ch in w)]
    return len(words) >= 2 and all(w.isupper() and len(w) > 1 for w in words[:2])


def _event_code(title, sub):
    """A short, stable code for an event, from its Archive labels."""
    t = title.strip().upper()
    s = (sub or "").strip()
    leg = re.search(r'(First|Second) Leg', s + " " + title, re.I)
    leg_n = {"FIRST": 1, "SECOND": 2}[leg.group(1).upper()] if leg else None
    game = re.search(r'Game (One|Two|Three)', s + " " + title, re.I)
    game_n = NUMBER_WORDS[game.group(1).upper()] if game else None

    m = re.match(r'(REGIONAL|LEAGUE) MATCHDAY (\w+)$', t)
    if m:
        return ("R" if m.group(1) == "REGIONAL" else "L") + str(NUMBER_WORDS[m.group(2)])
    m = re.match(r'GRAND SWISS ROUND (\w+)$', t)
    if m and m.group(1) in NUMBER_WORDS:
        return f"GS{NUMBER_WORDS[m.group(1)]}"
    if t == "CHAMPIONSHIP PLAY-IN":
        # S8: one two-legged round ("First Leg"); S7: two ("First Round - ...");
        # S6: a best of three ("Game One").
        rm = re.match(r'(First|Second) Round', s, re.I)
        return f"WC-PI{1 if rm.group(1).lower() == 'first' else 2}-{leg_n}" if rm else f"WC-PI-{leg_n or game_n}"
    # S4.
    m = re.match(r'MATCHDAY (\w+) \((SAT|SUN)\)$', t)
    if m:
        return f"MD{NUMBER_WORDS[m.group(1)]}"
    m = re.match(r'(UTL|MRL) MATCHDAY ([A-Z]) \(\w+\)$', t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r'WCS ROUND (\d)-([AB]) \(\w+\)$', t)
    if m:
        return f"WCS-R{m.group(1)}-{m.group(2)}"
    m = re.match(r'(UTL|MRL|WCS) (ROUND OF 16|QUARTERFINAL|SEMIFINAL|CHAMPIONSHIP) - (FIRST|SECOND) LEG$', t)
    if m:
        rnd = {"ROUND OF 16": "R16", "QUARTERFINAL": "QF", "SEMIFINAL": "SF",
               "CHAMPIONSHIP": "F"}[m.group(2)]
        return f"{m.group(1)}-{rnd}-{leg_n}"
    if t == "CONFERENCE CHAMPIONSHIP":
        return f"CC-{ {'Quarterfinal': 'QF', 'Semifinal': 'SF'}[s.split(' - ')[0]] }-{leg_n}"
    if t.endswith(" CONFERENCE CHAMPIONSHIP"):
        return "CC-F"
    m = re.match(r'PLAYOFFS - (RED|BLUE) (BRACKET|FINAL - (FIRST|SECOND) LEG)$', t)
    if m:
        if m.group(2) == "BRACKET":
            rnd = {"Round of 16": "R16", "Quarterfinal": "QF", "Semifinal": "SF"}[s.split(" - ")[0]]
        else:
            rnd = "F"
        return f"PO-{m.group(1)[0]}-{rnd}-{leg_n}"
    if t == "LEAGUE PLAYOFFS":
        # S6: two days ("First Leg"); S5: a semifinal and a final, two legs each.
        rm = re.match(r'(Semifinal|Final) -', s)
        return f"LP-{'SF' if rm.group(1) == 'Semifinal' else 'F'}-{leg_n}" if rm else f"LP-{leg_n}"
    # S5's WCS: a Nature (draw) and a Justice (process) bracket.
    m = re.match(r'WCS (PRELIMINARY|FIRST|SECOND|THIRD|FOURTH|FIFTH) ROUND(?: - (NATURE|JUSTICE))?$', t)
    if m:
        rnd = "PR" if m.group(1) == "PRELIMINARY" else f"R{ORDINAL_WORDS[m.group(1)]}"
        bracket = m.group(2) or s.split(" - ")[0].upper()
        return f"WCS-{rnd}-{bracket[0]}"
    if t == "WCS QUARTERFINAL":
        return f"WCS-QF-{s.split(' - ')[0][0]}-{leg_n}"
    if t.startswith("WCS SEMIFINAL"):
        return f"WCS-SF-{leg_n}"
    if t.startswith("WCS SECOND PLACE"):
        return f"WCS-2P-{leg_n}"
    m = re.match(r'MGGG PLAYOFF - R(\d) (FIRST|SECOND) LEG$', t)
    if m:
        return f"MGGG-R{m.group(1)}-{leg_n}"
    m = re.match(r'FINALS - (GREAT|ULTRA) CLASS PLAYOFFS$', t)
    if m:
        return f"WC-{m.group(1)[0]}-R{ORDINAL_WORDS[s.split()[0].upper()]}"
    if t == "GREAT CLASS - FOURTH ROUND RESET":
        return "WC-G-R4R"
    if t == "FINALS - MASTERS TOURNAMENT":
        rnd = {"Quarterfinal": "QF", "Semifinal": "SF"}[s.split(" - ")[0]]
        return f"WC-M-{rnd}-{game_n}"
    if t.startswith("CHAMPIONSHIP - GAME"):
        return f"WC-F-{game_n}"
    stage = {
        "GRAND SWISS PLAY-IN ROUND": "GS-PI", "GRAND SWISS ROUND OF SIXTEEN": "GS-R16",
        "GRAND SWISS QUARTERFINAL": "GS-QF", "GRAND SWISS SEMIFINAL": "GS-SF",
    }.get(t)
    if stage:
        # S6's Grand Swiss play-in was single games, no legs.
        return f"{stage}-{leg_n}" if leg_n else stage
    if t.startswith("GRAND SWISS FINAL"):
        return f"GS-F-{leg_n}"
    if t == "REGIONAL PLAYOFFS":
        if s == "First Round":   # S5: a single-game first round
            return "RP-R1"
        rnd = {"Quarterfinal": "QF", "Semifinal": "SF", "Championship": "F"}[s.split(" - ")[0]]
        return f"RP-{rnd}-{leg_n}"
    if t == "CHAMPIONSHIP SWISS":
        # "Round One" (S8), "Round 3" (S7), "Round 3 - First Leg" (S7's
        # two-legged brackets).
        rm = re.match(r'Round (\w+)', s)
        n = int(rm.group(1)) if rm.group(1).isdigit() else NUMBER_WORDS[rm.group(1).upper()]
        return f"WC-S{n}" + (f"-{leg_n}" if leg_n else "")
    if t == "CHAMPIONSHIP FINALS":
        rnd = {"Play-in": "FPI", "Round of Sixteen": "R16", "Round of 16": "R16",
               "Quarterfinal": "QF", "Semifinal": "SF"}[s.split(" - ")[0]]
        return f"WC-{rnd}-{leg_n or game_n}"
    if t.startswith("WORLD CHAMPIONSHIP - GAME"):
        return f"WC-F-{game_n}"
    raise ValueError(f"unrecognised event: {title!r} / {sub!r}")


# ---------------------------------------------------------------------------
# Reading the workbook
# ---------------------------------------------------------------------------

def read_teams(wb, cfg):
    c = cfg["rankings"]
    teams = {}
    for rownum, r in enumerate(wb["Rankings"].iter_rows(min_row=2, max_row=161, values_only=True), 2):
        if r[c["dex"]] is None:
            continue
        if r[c["checkpoint_name"]] != r[c["name"]]:
            raise ValueError(f"Rankings row {rownum}: rank checkpoints are keyed to "
                             f"{r[c['checkpoint_name']]!r}, not {r[c['name']]!r}")
        region = r[c["region"]]
        accolades = [str(r[i]).strip() for i in c["accolades"]
                     if r[i] not in (None, "", "-")]
        if "division_letter" in c:
            tier_name = r[c["division"]]
            division = " ".join(str(x) for x in (tier_name, r[c["division_letter"]]) if x)
            tier = LEAGUE_TIER[tier_name] if tier_name else ""
        else:
            division, tier = LEAGUE_DIVISION_MAP[r[c["division"]]], ""
        teams[r[c["dex"]]] = {
            "dex": r[c["dex"]], "name": r[c["name"]],
            "region": REGION_NAME_FIX.get(region, region),
            "division": division, "tier": tier,
            "primary_type": r[c["primary"]] if c["primary"] is not None else "",
            "basclm": r[c["basclm"]] if c["basclm"] is not None else "",
            "opening_rank": r[c["opening_rank"]],
            "sheet_opening_elo": r[c["opening_elo"]] if c["opening_elo"] is not None else None,
            "final_rank": r[c["final_rank"]], "final_ovr": r[c["ovr"]],
            "final_elo": r[c["elo"]],
            "accolades": "; ".join(accolades),
            "record": {k: r[i] for k, i in cfg["record_cols"].items()},
        }
    if "opening_elo_sheet" in cfg:
        sheet, lo, hi, dex_col, elo_col = cfg["opening_elo_sheet"]
        for r in wb[sheet].iter_rows(min_row=lo, max_row=hi, values_only=True):
            if r[dex_col] is not None:
                teams[r[dex_col]]["sheet_opening_elo"] = r[elo_col]
    return teams


def apply_corrections(games, corrections):
    """Replace each ruled-wrong value, refusing if the sheet no longer holds
    the value the correction was written against."""
    by_row = {g["row"]: g for g in games}
    for row, (field, sheet, fixed, _why) in corrections.items():
        g = by_row.get(row)
        if g is None or g[field] != sheet:
            raise SystemExit(f"correction at Archive row {row}: expected {field}={sheet!r}, "
                             f"sheet has {None if g is None else g[field]!r}")
        g[field] = fixed


def opening_elos(games, teams, cfg=None):
    """Each team's Elo before its first game, from that game: Elo after, less
    the K it gained (winner) or plus the K it lost (loser). (S5 applied Elo out
    of Archive order later in the season, but a team's first game precedes all
    of that; its opening Elo still totals 288,000.)"""
    name_to_dex = {t["name"]: d for d, t in teams.items()}
    out = {}
    for g in games:
        k = float(g["elo_k"])
        for name, s, sign in ((g["winner_name"], g["w"], -1), (g["loser_name"], g["l"], 1)):
            d = name_to_dex[name]
            if d not in out:
                out[d] = float(s[9]) + sign * k
    return out


def read_games(wb, cfg, name_to_dex, region_of=None):
    """Every Archive game, in sheet order, with its event and play day.

    Each game's winner and loser are 10-field tuples in the packed string's own
    order -- (result, type, opponent dex, PF, PA, "^", spread, interest, DSCR,
    Elo after) -- as text. A full game string supplies them; for S5's short
    form ("3R") they come from the row's columns, and the two the columns do
    not hold ("^" and Elo after) are left blank."""
    cols = cfg["archive_cols"]
    overrides = cfg.get("label_overrides", {})
    inline = cfg.get("inline_events")
    rows = list(wb["Archive"].iter_rows(min_row=1, max_col=23, values_only=True))
    events, games = [], []
    cur = None

    def new_event(title, rownum, sub=None):
        e = {"title": title, "sub": sub, "row": rownum, "games": 0, "notes": [], "dates": []}
        events.append(e)
        return e

    day = None
    cell_fixes = cfg.get("cell_corrections", {})
    for rownum, r in enumerate(rows, start=1):
        if rownum == 1:
            continue
        if rownum in cell_fixes:
            key, sheet, fixed, _why = cell_fixes[rownum]
            if r[cols[key]] != sheet:
                raise SystemExit(f"cell correction at Archive row {rownum}: expected "
                                 f"{key}={sheet!r}, sheet has {r[cols[key]]!r}")
            r = list(r)
            r[cols[key]] = fixed
        # Column A marks the start of a play day ("D1 5/22"); every game below
        # it until the next mark was played that day.
        if isinstance(r[cols["date"]], str) and r[cols["date"]].strip():
            day = r[cols["date"]].strip()
        label = r[cols["label"]]
        if rownum in overrides:
            label, note = overrides[rownum]
            if note and cur is not None:
                cur["notes"].append(note)
        target = cur
        if isinstance(label, str) and label.strip():
            label = label.strip()
            if inline is not None and inline.match(label):
                # A one-row event written among another's games: this row's
                # game belongs to it, and the surrounding event carries on.
                target = new_event(label, rownum)
            elif _is_title(label, cfg):
                cur = target = new_event(label, rownum)
            elif cur is not None and STRUCTURAL.search(label):
                # A round/leg label. The first one refines the event; a second
                # opens the next part of the same stage (S7's Championship Swiss
                # "Round 3" single games, then "Round 3 - First Leg").
                if cur["sub"] is None:
                    cur["sub"] = label
                else:
                    cur = target = new_event(cur["title"], rownum, sub=label)
            elif cur is not None:
                # A changelog note or a matchday's name ("Base-2 Scoring
                # System", "The Overhaul").
                cur["notes"].append(label)
        winner, loser = r[cols["winner"]], r[cols["loser"]]
        if cols["w"] is None:
            # S4: no strings in the Archive at all -- a game row is one with a
            # winner; result and type are filled in below and from Calcs.
            if not winner:
                continue
            sw, sl = "", ""
        elif not r[cols["w"]]:
            continue
        else:
            sw, sl = str(r[cols["w"]]).strip(), str(r[cols["l"]]).strip()
        fw, fl = GAME_PATTERN.match(sw), GAME_PATTERN.match(sl)
        if fw and fl:
            w, l, short = fw.groups(), fl.groups(), False
        else:
            if cols["w"] is None:
                mw = ml = None
                kinds = (("3", "?"), ("0", "?"))
            else:
                mw, ml = SHORT_PATTERN.match(sw), SHORT_PATTERN.match(sl)
                kinds = ((mw.group(1), mw.group(2)), (ml.group(1), ml.group(2))) if mw and ml else None
            pair = DSCR_PAIR_PATTERN.match(str(r[cols["dscr"]]))
            if not (kinds and pair):
                raise ValueError(f"Archive row {rownum}: unparseable game {sw!r} / {sl!r}")
            spread = round(float(r[cols["spread"]]), 2)
            gi = str(round(float(r[cols["interest"]])))
            res_w, res_l = kinds[0][0].replace("O", "0"), kinds[1][0].replace("O", "0")
            w = (res_w, kinds[0][1], str(name_to_dex[loser]), str(r[cols["score_w"]]),
                 str(r[cols["score_l"]]), "", f"{spread:g}", gi, pair.group(1), "")
            l = (res_l, kinds[1][1], str(name_to_dex[winner]), str(r[cols["score_l"]]),
                 str(r[cols["score_w"]]), "", f"{-spread:g}", gi, pair.group(2), "")
            short = True
        target["games"] += 1
        if day and day not in target["dates"]:
            target["dates"].append(day)
        ex = None
        if cols["ex"] is not None and r[cols["ex"]] not in (None, ""):
            m = EX_PATTERN.match(str(r[cols["ex"]]).strip())
            if not m:
                raise ValueError(f"Archive row {rownum}: unparseable EX {r[cols['ex']]!r}")
            ex = int(m.group(1))
        games.append({
            "row": rownum, "event": target, "winner_name": winner, "loser_name": loser,
            "home_away": str(r[cols["ha"]]).strip().upper(), "w": w, "l": l, "short": short,
            "raw_w": sw, "raw_l": sl, "string_source": "archive",
            "ex": ex, "mark": r[cols["mark"]] or "", "note": r[cols["note"]] or "", "day": day or "",
            "rank_w": r[cols["rank_w"]], "rank_l": r[cols["rank_l"]], "elo_k": r[cols["k"]],
            "goals": r[cols["goals"]],
            "col": {"score": (r[cols["score_w"]], r[cols["score_l"]]),
                    "spread": r[cols["spread"]], "interest": r[cols["interest"]],
                    "dscr": r[cols["dscr"]]},
        })

    code_overrides = cfg.get("event_code_overrides", {})
    for e in events:
        e["code"] = code_overrides.get(e["title"]) or _event_code(e["title"], e["sub"])
    # Back-to-back blocks under the same label are one event (S5 wrote one WCS
    # round as two blocks on different days); a code seen again later gets a
    # ".2", ".3" suffix so every event's code stays unique.
    merged = []
    for e in events:
        if merged and merged[-1]["code"] == e["code"]:
            keep = merged[-1]
            keep["games"] += e["games"]
            keep["notes"] += e["notes"]
            keep["dates"] += [d for d in e["dates"] if d not in keep["dates"]]
            for g in games:
                if g["event"] is e:
                    g["event"] = keep
        else:
            merged.append(e)
    seen = Counter()
    for i, e in enumerate(merged, start=1):
        e["round"] = i
        seen[e["code"]] += 1
        if seen[e["code"]] > 1:
            e["code"] = f"{e['code']}.{seen[e['code']]}"

    # S4: the game type comes from the event, since no string carries it.
    rules = cfg.get("type_by_code")
    if rules:
        for g in games:
            code = g["event"]["code"]
            gtype = next((t for rx, t in rules if re.match(rx, code)), None)
            if gtype is None:
                raise ValueError(f"no game type rule for event {code}")
            if gtype == "R/I":
                same = region_of[g["winner_name"]] == region_of[g["loser_name"]]
                gtype = "R" if same else "I"
            g["w"] = (g["w"][0], gtype) + tuple(g["w"][2:])
            g["l"] = (g["l"][0], gtype) + tuple(g["l"][2:])
    return merged, games


def read_calcs_grid(wb, cfg):
    """{season dex: [(column, slot header, string, groups)]} -- each team's full
    game strings from Calcs' per-team grid, in slot order."""
    lo, hi = cfg["calcs_grid"]
    ws = wb["Calcs"]
    header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    grid = {}
    for r in ws.iter_rows(min_row=3, max_row=162, values_only=True):
        if r[0] is None:
            continue
        cells = []
        for j in range(lo, hi):
            v = r[j]
            if not isinstance(v, str):
                continue
            if cfg.get("calcs_pattern") == "s4":
                m = CALCS_S4_PATTERN.match(v.strip())
                if m:
                    # Normalised to the packed-string order; the type is the
                    # event's ("?" here), the side is kept as an 11th field.
                    side, res, *rest = m.groups()
                    cells.append((j, header[j], v.strip(),
                                  ("3" if res == "W" else "0", "?", *rest, side)))
                continue
            m = GAME_PATTERN.match(v.strip())
            if m:
                cells.append((j, header[j], v.strip(), m.groups()))
        grid[r[0]] = cells
    return grid


def match_calcs(games, grid, cfg, name_to_dex):
    """Find each game's two strings in Calcs: in the winner's and loser's own
    rows by opponent and score, or -- for a misfiled column -- anywhere in that
    column. Returns ({game row: (winner cell, loser cell)}, [unmatched rows])."""
    misfiled = set(cfg.get("calcs_misfiled", {}))
    used, found, missing = set(), {}, []
    by_col = defaultdict(list)
    for d, cells in grid.items():
        for c in cells:
            by_col[c[1]].append((d, c))

    def own_row(d, opp, pf, pa):
        for c in grid.get(d, []):
            if (d, c[0]) not in used and int(c[3][2]) == opp and (c[3][3], c[3][4]) == (pf, pa):
                return (d, c)
        return None

    def in_column(slot, groups):
        for d, c in by_col.get(slot, []):
            if (d, c[0]) not in used and (c[3][0],) + tuple(c[3][2:9]) == (groups[0],) + tuple(groups[2:9]):
                return (d, c)
        return None

    aggot = cfg.get("aggot_rows", {})

    def own_row_any_score(d, opp, pf, pa):
        # An AggOT leg: Calcs' score is the regulation score plus AggOT points.
        for c in grid.get(d, []):
            if ((d, c[0]) not in used and int(c[3][2]) == opp
                    and int(c[3][3]) >= int(pf) and int(c[3][4]) >= int(pa)):
                return (d, c)
        return None

    # AggOT legs last, so the same pairing's other leg is matched exactly first.
    for g in sorted(games, key=lambda g: g["row"] in aggot):
        wd, ld = name_to_dex[g["winner_name"]], name_to_dex[g["loser_name"]]
        finder = own_row_any_score if g["row"] in aggot else own_row
        cw = finder(wd, ld, g["w"][3], g["w"][4])
        cl = finder(ld, wd, g["l"][3], g["l"][4])
        if (cw is None or cl is None) and g["event"]["code"] in misfiled and not g["short"]:
            cw = cw or in_column(g["event"]["code"], g["w"])
            cl = cl or in_column(g["event"]["code"], g["l"])
        if cw is None or cl is None:
            missing.append(g["row"])
            continue
        used.add((cw[0], cw[1][0]))
        used.add((cl[0], cl[1][0]))
        found[g["row"]] = (cw[1], cl[1])
    return found, missing


def fill_short_from_calcs(games, grid, cfg, name_to_dex):
    """Complete each short-form game from its Calcs strings (the Archive holds
    only "3R"/"0R" for them). Results and scores must agree with the Archive's."""
    found, missing = match_calcs(games, grid, cfg, name_to_dex)
    for g in games:
        if not g["short"]:
            continue
        if g["row"] in missing:
            raise SystemExit(f"Archive row {g['row']}: short-form game has no Calcs strings")
        cw, cl = found[g["row"]]
        if (cw[3][0], cl[3][0]) != (g["w"][0], g["l"][0]):
            raise SystemExit(f"Archive row {g['row']}: Calcs result {cw[2]} disagrees with the Archive")
        if len(cw[3]) > 10:
            # S4: the strings name the winner's side; it must be the Archive's.
            if cw[3][10] != g["home_away"] or cl[3][10] == cw[3][10]:
                raise SystemExit(f"Archive row {g['row']}: Calcs sides {cw[2]} / {cl[2]} "
                                 f"disagree with the Archive's {g['home_away']}")
        keep_type = g["w"][1]
        reg_w, reg_l = (g["w"][3], g["w"][4]), (g["l"][3], g["l"][4])
        g["w"] = (cw[3][0], cw[3][1] if cw[3][1] != "?" else keep_type) + tuple(cw[3][2:10])
        g["l"] = (cl[3][0], cl[3][1] if cl[3][1] != "?" else keep_type) + tuple(cl[3][2:10])
        if g["row"] in cfg.get("aggot_rows", {}):
            g["aggot"] = (int(g["w"][3]) - int(reg_w[0]), int(g["w"][4]) - int(reg_w[1]))
            g["w"] = g["w"][:3] + reg_w + g["w"][5:]
            g["l"] = g["l"][:3] + reg_l + g["l"][5:]
        g["raw_w"], g["raw_l"] = cw[2], cl[2]
        g["string_source"] = "calcs"


def read_walkovers(wb, cfg, events):
    """Calcs' "3S-" cells: byes scored as wins. The Calcs column's header names
    the slot (S1, S2...), which is matched to that Grand Swiss round's event."""
    ws = wb["Calcs"]
    first = cfg["calcs_first_col"]
    header = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    by_code = {e["code"]: e for e in events}
    out = []
    for r in ws.iter_rows(min_row=3, max_row=162, values_only=True):
        if r[0] is None:
            continue
        for j in range(first - 1, len(r)):
            v = r[j]
            if not isinstance(v, str):
                continue
            m = WALKOVER_PATTERN.match(v)
            if not m:
                continue
            slot = header[j]
            sm = re.match(r'^S(\d+)$', str(slot))
            if not sm or f"GS{sm.group(1)}" not in by_code:
                raise ValueError(f"walkover {v!r} for dex {r[0]} in unexpected Calcs slot {slot!r}")
            ev = by_code[f"GS{sm.group(1)}"]
            out.append({"dex": r[0], "result": int(m.group(1)), "game_type": m.group(2),
                        "round": ev["round"], "event": ev["code"]})
    return out


# ---------------------------------------------------------------------------
# Identity: the season's dex -> canonical (S9) id
# ---------------------------------------------------------------------------

def canonical_ids(teams):
    s9 = json.load(open(os.path.join(ROOT, "archive", "s9_inputs.json")))["teams"]
    by_name = {t["name"]: int(dex) for dex, t in s9.items()}
    out = {}
    for dex, t in teams.items():
        name = RENAMED_SINCE.get(t["name"], t["name"])
        if name not in by_name:
            raise ValueError(f"{t['name']!r} (dex {dex}) has no S9 counterpart; add it to RENAMED_SINCE")
        out[dex] = by_name[name]
    if len(set(out.values())) != len(out):
        raise ValueError("two of the season's teams map to the same S9 team")
    if len(out) == len(by_name) and sorted(out.values()) != sorted(by_name.values()):
        raise ValueError("the season's teams do not map one-to-one onto S9's")
    return out, {cid: s9[str(cid)]["name"] for cid in out.values()}


# ---------------------------------------------------------------------------
# Building rows and validating
# ---------------------------------------------------------------------------

def build_game_rows(games, name_to_dex, canon, cfg):
    rows = []
    for seq, g in enumerate(games, start=1):
        w, l = g["w"], g["l"]
        wdex, ldex = name_to_dex[g["winner_name"]], name_to_dex[g["loser_name"]]
        # An unknown host (S3's earliest games) keeps the winner as team_a and
        # leaves `home` blank.
        winner_home = g["home_away"] in ("H", "")
        home, away = (w, l) if winner_home else (l, w)
        rank_h, rank_a = (g["rank_w"], g["rank_l"]) if winner_home else (g["rank_l"], g["rank_w"])
        gw, gl = (int(x) for x in GOALS_PATTERN.match(str(g["goals"])).groups())
        goals_h, goals_a = (gw, gl) if winner_home else (gl, gw)
        hdex, adex = (wdex, ldex) if winner_home else (ldex, wdex)
        ot = "OT" in g["note"] or (int(w[0]) == 2)
        rows.append({
            "seq": seq, "round": g["event"]["round"], "event": g["event"]["code"], "day": g["day"],
            "game_type": w[1],
            "team_a": canon[hdex], "team_b": canon[adex],
            "result_a": int(home[0]), "pf_a": int(home[3]), "pa_a": int(home[4]),
            # S5's "^" is goals (already in goals_a/goals_b); the modern raw goal
            # score was not recorded.
            "raw_goal_a": "" if cfg.get("caret_is_goals") else home[5],
            "raw_goal_b": "" if cfg.get("caret_is_goals") else away[5],
            "spread_a": home[6], "interest": home[7],
            "dscr_a": home[8], "dscr_b": away[8],
            "elo_a_after": home[9], "elo_b_after": away[9],
            "home": "A" if g["home_away"] else "",
            "host_source": g.get("host_source", "recorded"),
            # Modern per-team EX is a gap: S7-S8 banked a bonus for the winner
            # only, as a differential, not each side's own score; S6 kept no
            # per-game EX at all.
            "ex_a": "", "ex_b": "",
            "ex_winner_diff": "" if g["ex"] is None else g["ex"],
            "winner": "A" if winner_home else "B",
            "goals_a": goals_h, "goals_b": goals_a, "elo_k": g["elo_k"],
            "aggot_a": (g["aggot"][0] if winner_home else g["aggot"][1]) if "aggot" in g else "",
            "aggot_b": (g["aggot"][1] if winner_home else g["aggot"][0]) if "aggot" in g else "",
            "rank_a": rank_h, "rank_b": rank_a,
            "ot": int(ot), "mark": g["mark"], "note": g["note"].replace("\n", " "),
            "source_row": g["row"], "string_source": g["string_source"],
            "raw_winner": g["raw_w"], "raw_loser": g["raw_l"],
        })
    return rows


GAME_FIELDS = ["seq", "round", "event", "day", "game_type", "team_a", "team_b", "result_a",
               "pf_a", "pa_a", "raw_goal_a", "raw_goal_b", "spread_a", "interest",
               "dscr_a", "dscr_b", "elo_a_after", "elo_b_after", "home", "host_source", "ex_a", "ex_b",
               "ex_winner_diff", "winner", "goals_a", "goals_b", "aggot_a", "aggot_b", "elo_k",
               "rank_a", "rank_b",
               "ot", "mark", "note", "source_row", "string_source", "raw_winner", "raw_loser"]


def validate(season, teams, games, walkovers, canon, events, anomalies=None, cfg=None):
    """Every check must pass or nothing is written. Returns (errors, accepted):
    an error on a row listed in `anomalies` under its check is accepted, and
    so is reported rather than refused."""
    cfg = cfg if cfg is not None else SEASONS[season]
    errors = []
    name_to_dex = {t["name"]: d for d, t in teams.items()}
    opening = opening_elos(games, teams, cfg)

    # 1. Both sides of every game agree, and the strings name the right teams.
    for g in games:
        w, l = g["w"], g["l"]
        where = f"row {g['row']}"
        if int(w[0]) + int(l[0]) != 3:
            errors.append(f"{where}: results {w[0]}/{l[0]} do not sum to 3")
        if (w[3], w[4]) != (l[4], l[3]):
            errors.append(f"{where}: scores do not mirror")
        if w[6] != "" and abs(float(w[6]) + float(l[6])) > 0.011:
            errors.append(f"{where}: spreads do not mirror")
        if w[7] != l[7] or w[1] != l[1]:
            errors.append(f"{where}: interest or game type differ")
        if int(w[0]) < 2:
            errors.append(f"{where}: the named winner did not win")
        # (S1-S2 identify a team by its Rankings code, not a number.)
        if w[2] != str(name_to_dex.get(g["loser_name"])) or l[2] != str(name_to_dex.get(g["winner_name"])):
            errors.append(f"{where}: opponent dex does not match the named teams")
        optional = cfg.get("host_optional")
        if g["home_away"] not in ("H", "A") and not (g["home_away"] == "" and (
                optional is True or (optional and g["row"] in optional))):
            errors.append(f"{where}: host {g['home_away']!r} is neither H nor A")
        gm = GOALS_PATTERN.match(str(g["goals"]))
        if not gm:
            errors.append(f"{where}: goals {g['goals']!r} unparseable")
        elif int(gm.group(1)) > int(w[3]) or int(gm.group(2)) > int(l[3]):
            errors.append(f"{where}: more goals than points (goals {g['goals']}, score {w[3]}-{l[3]})")
        if not isinstance(g["elo_k"], (int, float)):
            errors.append(f"{where}: Elo K {g['elo_k']!r} is not a number")

        # 1b. Where a full game string exists, the row's own columns say the
        #     same thing -- which is what lets S5's short-form games be read
        #     from those columns alone.
        if not g["short"]:
            c = g["col"]
            if (int(w[3]), int(w[4])) != tuple(c["score"]):
                errors.append(f"{where}: score columns {c['score']} != string {w[3]}-{w[4]}")
            if abs(float(w[6]) - float(c["spread"])) > 0.006:
                errors.append(f"{where}: spread column {c['spread']} != string {w[6]}")
            if abs(float(w[7]) - float(c["interest"])) > 0.6:
                errors.append(f"{where}: interest column {c['interest']} != string {w[7]}")
            pair = DSCR_PAIR_PATTERN.match(str(c["dscr"]))
            if not pair or (float(pair.group(1)), float(pair.group(2))) != (float(w[8]), float(l[8])):
                errors.append(f"{where}: dscr column {c['dscr']} != strings {w[8]}/{l[8]}")
        if cfg.get("caret_is_goals") and gm and w[5] != "" and "aggot" not in g and (float(w[5]), float(l[5])) != (
                float(gm.group(1)), float(gm.group(2))):
            errors.append(f"{where}: goals column {g['goals']} != string ^{w[5]}/^{l[5]}")

    # 2. Per-team W-L in each bucket equals the workbook's own, byes included.
    bucket = cfg.get("record_buckets", DEFAULT_BUCKETS)
    rec = defaultdict(Counter)
    def buckets_of(gtype):
        b = bucket[gtype]
        return (b,) if isinstance(b, str) else b
    for g in games:
        for b in buckets_of(g["w"][1]):
            rec[name_to_dex[g["winner_name"]]][b + "W"] += 1
            rec[name_to_dex[g["loser_name"]]][b + "L"] += 1
    for wo in walkovers:
        for b in buckets_of(wo["game_type"]):
            rec[wo["dex"]][b + ("W" if wo["result"] >= 2 else "L")] += 1
    grid = cfg.get("_grid")
    if cfg.get("records_from_calcs"):
        # 2'. S5: Rankings' records come from Calcs' grid, whose R11 column is
        #     misfiled. So: Rankings == the grid's records exactly, and every
        #     Archive game equals its own Calcs strings (2'' below) -- which
        #     together prove the Archive's games are the season's.
        for dex, t in teams.items():
            grec = Counter(bucket[c[3][1]] + ("W" if int(c[3][0]) >= 2 else "L")
                           for c in grid.get(dex, []))
            for k, v in t["record"].items():
                if grec[k] != v:
                    errors.append(f"{t['name']}: {k} {grec[k]} from Calcs, {v} in Rankings")
    else:
        for dex, t in teams.items():
            for k, v in t["record"].items():
                if rec[dex][k] != v:
                    errors.append(f"{t['name']}: {k} {rec[dex][k]} from the games, {v} in Rankings")
    if grid is not None:
        found, missing = match_calcs(games, grid, cfg, name_to_dex)
        for row in missing:
            errors.append(f"row {row}: calcs has no matching strings")
        for g in games:
            if g["row"] in found:
                cw, cl = found[g["row"]]
                def norm(t, gt):
                    return (t[0], gt if t[1] == "?" else t[1]) + tuple(t[2:10])
                def body(t):
                    # An AggOT leg's score and "^" carry the AggOT points in Calcs.
                    return tuple(x for i, x in enumerate(t) if i not in (3, 4, 5)) if "aggot" in g else t
                if (body(norm(cw[3], g["w"][1])), body(norm(cl[3], g["l"][1]))) != (
                        body(tuple(g["w"])), body(tuple(g["l"]))):
                    errors.append(f"row {g['row']}: calcs strings {cw[2]} / {cl[2]} != "
                                  f"{g['raw_w']} / {g['raw_l']}")
        used = sum(len(v) for v in found.values())
        total = sum(len(c) for c in grid.values())
        if used != total:
            errors.append(f"Calcs holds {total} game strings, {used} matched to Archive games")

    # 3. The Elo chain: from each team's opening Elo, every game moves the
    #    winner up and the loser down by exactly that game's Elo K (Archive E,
    #    the points exchanged). Where the sheet also kept the carried-in Elo,
    #    the opening Elo the games imply must equal it.
    for d, t in teams.items():
        if t["sheet_opening_elo"] is not None and float(t["sheet_opening_elo"]) != opening.get(d):
            errors.append(f"{t['name']}: opening Elo {opening.get(d)} from the games, "
                          f"{t['sheet_opening_elo']} in the sheet")
    elo = dict(opening)
    for g in (games if cfg.get("elo_chain", True) else []):
        wd, ld = name_to_dex[g["winner_name"]], name_to_dex[g["loser_name"]]
        k = float(g["elo_k"]) if isinstance(g["elo_k"], (int, float)) else None
        if k is not None and (abs(elo[wd] + k - float(g["w"][9])) > 0.5
                              or abs(elo[ld] - k - float(g["l"][9])) > 0.5):
            errors.append(f"row {g['row']}: Elo {elo[wd]:.0f}/{elo[ld]:.0f} +/- K {k:g} != "
                          f"{g['w'][9]}/{g['l'][9]}")
        elo[wd], elo[ld] = float(g["w"][9]), float(g["l"][9])

    # 3b. Each team's last recorded Elo equals its final Elo.
    last = {}
    for g in games:
        if g["w"][9] != "":
            last[name_to_dex[g["winner_name"]]] = float(g["w"][9])
            last[name_to_dex[g["loser_name"]]] = float(g["l"][9])
    for dex, t in teams.items():
        if last.get(dex) != float(t["final_elo"]):
            errors.append(f"{t['name']}: last game Elo {last.get(dex)} != final {t['final_elo']}")

    # 3c. Elo is zero-sum and every season opens on an average of 1800: the
    #     opening Elo and the final Elo each total 1800 per team (per the user,
    #     2026-10-11: "as long as the Starting Elo totals 288000, it's fine").
    expected_total = cfg.get("elo_total", 1800 * len(teams))
    totals = [("final", [float(t["final_elo"]) for t in teams.values()])]
    if opening:
        totals.insert(0, ("opening", list(opening.values())))
    for label, vals in totals:
        total = sum(vals)
        if expected_total is not None and abs(total - expected_total) > 0.5:
            errors.append(f"{label} Elo totals {total:g}, not {expected_total}")

    # 4. The next season's own record of how this one ended: its opening rank
    #    and opening Elo, by canonical id. For S8 that is S9's "S8 End" ranks
    #    (copied verbatim from S9's workbook), which is what proves
    #    RENAMED_SINCE; for earlier seasons the next season's archived
    #    teams.csv. Required: a season is extracted only after the one after it.
    #    Where the next season added teams and ranked them in among the old
    #    ones (S3 -> S4's Phoenix, S2 -> S3's Dynamax) the check is the ORDER
    #    of this season's teams ("next_rank": "order"); where the next season
    #    kept no opening rank (S2) it is skipped ("next_rank": None).
    nxt = _next_season_record(season)
    rank_mode = cfg.get("next_rank", "exact")
    for dex, t in teams.items():
        rank, elo_in = nxt[canon[dex]]
        if rank_mode == "exact":
            if rank is None:
                errors.append(f"{t['name']}: S{season + 1} has no opening rank for it")
            elif rank != t["final_rank"]:
                errors.append(f"{t['name']}: final rank {t['final_rank']}, S{season + 1} opened it at {rank}")
        if elo_in is not None and elo_in != float(t["final_elo"]):
            errors.append(f"{t['name']}: final Elo {t['final_elo']}, S{season + 1} opened it at {elo_in}")
    if rank_mode == "order":
        mine = sorted(teams, key=lambda d: teams[d]["final_rank"])
        if any(nxt[canon[d]][0] is None for d in mine):
            errors.append(f"S{season + 1} has no opening rank for some of this season's teams")
        else:
            theirs = sorted(teams, key=lambda d: nxt[canon[d]][0])
            if mine != theirs:
                bad = [teams[d]["name"] for d, e in zip(mine, theirs) if d != e]
                errors.append(f"final rank order differs from S{season + 1}'s opening order "
                              f"({len(bad)} places, from {bad[0]})")

    # 5. The Hall of Fame's titles for this season follow from the games.
    errors += _check_hall_of_fame(season, games, teams, events, cfg)

    # 5b. Every event's games are of one game type -- which catches a game
    #     filed under the wrong event (S5's interleaved MGGG playoff games).
    types = defaultdict(set)
    for g in games:
        types[g["event"]["code"]].add(g["w"][1])
    for code, ts in types.items():
        if len(ts) > 1 and not ts <= cfg.get("mixed_types", set()):
            errors.append(f"{code}: mixes game types {sorted(ts)}")

    # 6. Regional and League are complete single round robins: each pair in a
    #    region (division) meets exactly once, never across groups, and every
    #    team plays every matchday.
    for gtype, key in cfg.get("round_robins", [("R", "region"), ("L", "division")]):
        pairs, per_event = Counter(), defaultdict(Counter)
        for g in games:
            if g["w"][1] != gtype:
                continue
            a, b = name_to_dex[g["winner_name"]], name_to_dex[g["loser_name"]]
            pairs[frozenset((a, b))] += 1
            per_event[g["event"]["code"]].update((a, b))
            if teams[a][key] != teams[b][key]:
                errors.append(f"row {g['row']}: {gtype} game across {key}s")
        sizes = Counter(t[key] for t in teams.values())
        expected = sum(n * (n - 1) // 2 for n in sizes.values())
        if len(pairs) != expected or any(v != 1 for v in pairs.values()):
            errors.append(f"{gtype}: {len(pairs)} distinct pairs (expected {expected}), "
                          f"{sum(v > 1 for v in pairs.values())} repeated")
        for code, c in per_event.items():
            if len(c) != len(teams) or set(c.values()) != {1}:
                errors.append(f"{code}: not every team played exactly once")

    # 6b. S1-S2: every division is a complete single round robin within the
    #     regular season, whichever weeks its games fell in.
    if cfg.get("division_rr"):
        pairs = Counter()
        for g in games:
            a, b = name_to_dex[g["winner_name"]], name_to_dex[g["loser_name"]]
            if g["w"][1] == "R" and teams[a]["division"] == teams[b]["division"]:
                pairs[frozenset((a, b))] += 1
        sizes = Counter((t["region"], t["division"]) for t in teams.values())
        expected = sum(n * (n - 1) // 2 for n in sizes.values())
        if len(pairs) != expected or any(v != 1 for v in pairs.values()):
            errors.append(f"divisions: {len(pairs)} distinct pairs (expected {expected}), "
                          f"{sum(v > 1 for v in pairs.values())} repeated")

    # 6c. Hosts the user declared, which fix the schedule's orientation.
    for host, away in cfg.get("host_declarations", []):
        found = [g for g in games if {g["winner_name"], g["loser_name"]} == {host, away}
                 and g["w"][1] == "R"]
        if not found:
            errors.append(f"declared host: no {host} v {away} game")
        for g in found:
            actual = g["winner_name"] if g["home_away"] == "H" else g["loser_name"]
            if actual != host:
                errors.append(f"declared host: {host} should host {away}, row {g['row']} has {actual}")

    # Listed anomalies: accepted, but each must actually occur -- a listed
    # anomaly that no longer fires means the list is stale.
    # An anomaly is keyed by Archive row (matched with its check's name) or,
    # for a team-level finding, by the start of the error's own text.
    anomalies = anomalies or {}
    accepted, fired = [], set()
    for e in errors:
        m = re.match(r'row (\d+): (\w+)', e)
        if m and int(m.group(1)) in anomalies and m.group(2) == anomalies[int(m.group(1))][0]:
            accepted.append(e)
            fired.add(int(m.group(1)))
            continue
        for key in anomalies:
            if isinstance(key, str) and e.startswith(key):
                accepted.append(e)
                fired.add(key)
    errors = [e for e in errors if e not in accepted]
    for row in anomalies:
        if row not in fired:
            errors.append(f"listed anomaly at row {row} did not occur")
    return errors, accepted


def _next_season_record(season):
    """{canonical id: (rank, Elo or None)} for how the NEXT season opened."""
    if season == 8:
        s8_end = json.load(open(os.path.join(ROOT, "seasons", "s9", "rank_history_verbatim.json")))["S8 End"]
        s9 = json.load(open(os.path.join(ROOT, "archive", "s9_inputs.json")))["teams"]
        return {int(d): (s8_end[t["name"]], None) for d, t in s9.items()}
    path = os.path.join(HISTORY, f"s{season + 1}", "teams.csv")
    if not os.path.exists(path):
        raise SystemExit(f"S{season + 1} must be extracted before S{season} (needs {os.path.relpath(path, ROOT)})")
    return {int(r["team"]): (int(r["opening_rank"]) if r["opening_rank"] else None,
                             float(r["opening_elo"]) if r["opening_elo"] else None)
            for r in csv.DictReader(open(path))}


def _tie_winner(games):
    """Aggregate over a two-legged tie; returns (winner, {name: points})."""
    pts = Counter()
    for g in games:
        pts[g["winner_name"]] += int(g["w"][3])
        pts[g["loser_name"]] += int(g["l"][3])
    (a, pa), (b, pb) = pts.most_common(2)
    return (a if pa != pb else None), pts


def derived_titles(games, teams, events, cfg):
    """Champions this season's games decide: the World Championship (best of
    three), the season's cup and each region's playoff (a two-legged final).
    The cup is the Grand Swiss's two-legged final (S6-S8), or S5's WCS: each
    bracket's final ("WCS SEMIFINAL") is two-legged, the team that won every
    one of them is champion, and the "Second Place" tie decides the runner-up.
    An exact aggregate tie is reported, not guessed."""
    by_code = defaultdict(list)
    for g in games:
        by_code[g["event"]["code"]].append(g)
    titles = {}

    world_codes = cfg.get("world_final", ("WC-F-1", "WC-F-2", "WC-F-3"))
    wc = Counter(g["winner_name"] for c in world_codes for g in by_code[c])
    finalists = {g[k] for c in world_codes for g in by_code[c]
                 for k in ("winner_name", "loser_name")}
    champ = wc.most_common(1)[0][0]
    titles["world"] = (champ, (finalists - {champ}).pop())

    cup_name, rule, *cup_codes = cfg["cup"] or (None, None)
    if rule == "two_leg_final":
        codes = cup_codes[0] if cup_codes else ["GS-F-1", "GS-F-2"]
        w, pts = _tie_winner([g for c in codes for g in by_code[c]])
        titles["cup"] = (w, (set(pts) - {w}).pop() if w else None)
    elif rule == "wcs":
        ties = defaultdict(list)
        for g in by_code["WCS-SF-1"] + by_code["WCS-SF-2"]:
            ties[frozenset((g["winner_name"], g["loser_name"]))].append(g)
        winners = {_tie_winner(gl)[0] for gl in ties.values()}
        champ = winners.pop() if len(winners) == 1 else None
        w, pts = _tie_winner(by_code["WCS-2P-1"] + by_code["WCS-2P-2"])
        titles["cup"] = (champ, w)
    titles["cup_name"] = cup_name

    region_of = {t["name"]: t["region"] for t in teams.values()}
    ties = defaultdict(list)
    for c in cfg.get("regional_final", ["RP-F-1", "RP-F-2"]):
        for g in by_code[c]:
            ties[region_of[g["winner_name"]]].append(g)
    titles["regions"] = {}
    for region, gl in ties.items():
        w, pts = _tie_winner(gl)
        titles["regions"][region] = (w, (set(pts) - {w}).pop() if w else None, dict(pts))
    titles["league"] = []
    for codes in cfg.get("league_finals", []):
        w, pts = _tie_winner([g for c in codes for g in by_code[c]])
        titles["league"] += [w, (set(pts) - {w}).pop() if w else None]
    return titles


def _check_hall_of_fame(season, games, teams, events, cfg):
    hof = json.load(open(os.path.join(ROOT, "hall_of_fame.json")))
    s = str(season)
    errors = []
    titles = derived_titles(games, teams, events, cfg)

    def hof_names(entry):
        # "*" marks "since renamed or moved conference" in the Hall of Fame.
        return [RENAMED_SINCE.get(t["name"].rstrip("*"), t["name"].rstrip("*")) if t else None
                for t in entry["teams"]]

    def canonical(n):
        return RENAMED_SINCE.get(n, n)

    world = hof_names(hof["plaques"]["world"]["seasons"][s])
    if [canonical(x) for x in titles["world"]] != world[:2]:
        errors.append(f"World Championship: games say {titles['world']}, Hall of Fame {world[:2]}")

    cup = hof["plaques"]["cup"]["seasons"].get(s)
    if cfg["cup"] is None:
        if cup is not None:
            errors.append(f"the Hall of Fame has an S{s} cup but this season names none")
    elif [canonical(x) if x else None for x in titles["cup"]] != hof_names(cup)[:2]:
        errors.append(f"{titles['cup_name']}: games say {titles['cup']}, Hall of Fame {hof_names(cup)}")

    if titles["league"]:
        league = hof_names(hof["plaques"]["league"]["seasons"][s])
        if [canonical(x) if x else None for x in titles["league"]] != league[:len(titles["league"])]:
            errors.append(f"League titles: games say {titles['league']}, Hall of Fame {league}")

    for region, (w, ru, pts) in titles["regions"].items():
        if w is None:
            errors.append(f"{region} regional final is level on aggregate {pts}: no rule to settle it")
            continue
        plate = hof["regions"].get(region, {}).get(s)
        if plate is None:
            errors.append(f"{region}: no Hall of Fame entry for S{s}")
            continue
        champ = plate["name"].rstrip("*")
        if canonical(w) != RENAMED_SINCE.get(champ, champ):
            errors.append(f"{region} regional champion: games say {w}, Hall of Fame {champ}")
    return errors


# ---------------------------------------------------------------------------

def _num(x):
    """Write a whole-number float as an int (1834.0 -> 1834), as the sheet does."""
    return int(x) if float(x).is_integer() else x


def write_csv(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(season, workbook):
    cfg = SEASONS[season]
    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    sources = [workbook]
    host_errors = []
    if cfg.get("reader") == "early":
        # S1-S3: one workbook for all three, their Elo in a second.
        import history_early
        elo_path = os.path.join(ROOT, cfg["elo_workbook"])
        sources.append(elo_path)
        ewb = openpyxl.load_workbook(elo_path, read_only=True, data_only=True)
        teams = history_early.read_teams(wb, ewb, cfg)
        events, games = history_early.read_games(wb, ewb, cfg, teams)
        if cfg.get("schedule"):
            hosts, host_errors, _ = history_early.schedule_hosts(wb, cfg, teams, games)
            history_early.apply_hosts(hosts, games)
    else:
        teams = read_teams(wb, cfg)
        events, games = read_games(wb, cfg, {t["name"]: d for d, t in teams.items()},
                                   {t["name"]: t["region"] for t in teams.values()})
    corrections = cfg["corrections"]
    apply_corrections(games, corrections)
    if "calcs_grid" in cfg:
        name_to_dex = {t["name"]: d for d, t in teams.items()}
        cfg["_grid"] = read_calcs_grid(wb, cfg)
        if cfg.get("fill_short_from_calcs"):
            fill_short_from_calcs(games, cfg["_grid"], cfg, name_to_dex)
    walkovers = [] if cfg.get("reader") == "early" else read_walkovers(wb, cfg, events)
    canon, canon_names = canonical_ids(teams)

    anomalies = cfg["anomalies"]
    errors, accepted = validate(season, teams, games, walkovers, canon, events, anomalies, cfg)
    errors = host_errors + errors
    for e in accepted:
        print("  accepted (listed anomaly): " + e)
    if errors:
        print(f"REFUSING to write S{season}: {len(errors)} check(s) failed")
        for e in errors[:50]:
            print("  " + e)
        sys.exit(1)

    out = os.path.join(HISTORY, f"s{season}")
    os.makedirs(out, exist_ok=True)
    name_to_dex = {t["name"]: d for d, t in teams.items()}

    write_csv(os.path.join(out, "games.csv"), GAME_FIELDS, build_game_rows(games, name_to_dex, canon, cfg))
    write_csv(os.path.join(out, "walkovers.csv"),
              ["round", "event", "team", "game_type", "result"],
              [{"round": w["round"], "event": w["event"], "team": canon[w["dex"]],
                "game_type": w["game_type"], "result": w["result"]}
               for w in sorted(walkovers, key=lambda w: (w["round"], canon[w["dex"]]))])
    opening = opening_elos(games, teams, cfg)
    team_fields = ["team", "season_dex", "name", "region", "division", "tier", "primary_type",
                   "basclm", "opening_rank", "opening_elo", "final_rank", "final_ovr",
                   "final_elo", "accolades"]
    write_csv(os.path.join(out, "teams.csv"), team_fields, [
        {"team": canon[d], "season_dex": d, "name": t["name"], "region": t["region"],
         "division": t["division"], "tier": t["tier"], "primary_type": t["primary_type"], "basclm": t["basclm"],
         "opening_rank": t["opening_rank"], "opening_elo": _num(opening[d]) if d in opening else "", "final_rank": t["final_rank"], "final_ovr": t["final_ovr"], "final_elo": t["final_elo"],
         "accolades": t["accolades"].replace("\n", " ")}
        for d, t in sorted(teams.items(), key=lambda kv: canon[kv[0]])])

    srcs = [{"workbook": os.path.relpath(p, ROOT), "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()}
            for p in sources]
    titles = derived_titles(games, teams, events, cfg)
    with open(os.path.join(out, "events.json"), "w") as f:
        json.dump({
            "season": season,
            "source": srcs[0] if len(srcs) == 1 else srcs,
            **({"host_sources": dict(sorted(Counter(g.get("host_source", "recorded")
                                                    for g in games).items()))}
               if cfg.get("reader") == "early" else {}),
            "games": len(games), "walkovers": len(walkovers),
            "titles": {"world": titles["world"],
                       **({titles["cup_name"]: titles["cup"]} if cfg["cup"] else {}),
                       **({"league": titles["league"]} if titles["league"] else {}),
                       "regions": {r: v[:2] for r, v in sorted(titles["regions"].items())}},
            "anomalies": [{"source_row" if isinstance(r, int) else "finding": r, "check": c,
                           "reason": why}
                          for r, (c, why) in sorted(anomalies.items(), key=lambda kv: str(kv[0]))],
            "corrections": [{"source_row": r, "field": f, "sheet": a, "corrected": b, "reason": why}
                            for r, (f, a, b, why) in sorted({**corrections,
                                                            **cfg.get("cell_corrections", {})}.items())],
            "events": [{"round": e["round"], "code": e["code"], "title": e["title"],
                        "sub": e["sub"], "dates": e["dates"], "notes": e["notes"],
                        "games": e["games"], "empty": e["games"] == 0,
                        "source_row": e["row"]} for e in events],
        }, f, indent=1)
        f.write("\n")

    # The cross-season identity map.
    tpath = os.path.join(HISTORY, "teams.json")
    ident = json.load(open(tpath)) if os.path.exists(tpath) else {}
    for d, t in teams.items():
        cid = str(canon[d])
        entry = ident.setdefault(cid, {"s9_name": canon_names[canon[d]], "seasons": {}})
        entry["seasons"][str(season)] = {"dex": d, "name": t["name"], "region": t["region"],
                                         "division": t["division"]}
    ident = {k: ident[k] for k in sorted(ident, key=int)}
    with open(tpath, "w") as f:
        json.dump(ident, f, indent=1)
        f.write("\n")

    print(f"S{season}: {len(games)} games, {len(walkovers)} walkovers, {len(events)} events "
          f"({sum(e['games'] == 0 for e in events)} empty), {len(teams)} teams -> {os.path.relpath(out, ROOT)}/")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(int(sys.argv[1]), sys.argv[2])
