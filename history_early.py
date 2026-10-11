"""Seasons 1-3 for extract_history.py: one workbook holds all three, and a
second holds their Elo.

- history/s1-3/Dice_Game_S123.xlsx -- the games ("S1 Archive", "S2 Archive",
  and "Archive" for S3), the rankings ("S1 Rankings", "S2 Rankings",
  "Rankings"), S1's interconference schedule ("S1 InterC") and the division
  schedule template ("S2 Divisions").
- history/s1-3/DG_MRLUCL_Experimenting.xlsx -- "Season 1/2/3": every team's Elo
  after every week, and its own copies of the three archives, which carry the
  week each game was played in. S4's opening Elo came from here.

No game string survives for any of the three seasons, so each game is built
from its row's columns (as S4's were before Calcs filled them), its game type
from its event, and its Elo from the weekly sheet: each team plays at most once
a week, so a game's "Elo after" is its team's figure for that week and the
game's K is the change.

Hosts: S3 recorded H/A for most games; its 224 unrecorded conference games
take the pod schedule's host ("schedule", see pod_hosts), which first has to
reproduce every recorded conference host. S1 and S2 recorded none; their hosts
are rebuilt from the season's own schedule documents under rules the user set
(2026-10-11), and each game says which in `host_source`:

- "schedule": S2 Divisions' template. Each division plays the seven rounds of
  the template (cells G15:M18, "x=y"), by its position 1-8. S2's positions are
  the sheet's own lists; S1's are the order of its teams' codes in S1 Rankings
  column A, the one order of each division that fits the template (the 8
  symmetries otherwise give 8 different host patterns). The user's
  declaration fixes the orientation: Rustboro City hosted Verdanturf Town in
  S3's rivalry game, so Verdanturf hosted in S2 ("1=3": the SECOND team hosts)
  and Rustboro in S1 (the FIRST team hosts -- "reversed for S1"). Crossovers
  use the rounds the template letters A (S1: rounds 1 and 7) or B (S2: rounds
  3 and 5) across the two divisions; S2's interconference games use round 4's
  ("4-X") pairs between the conferences matched that week.
- "interc": S1's interconference games, from S1 InterC.
- "record": a conference championship, hosted by the better regular-season
  record; "poll" where the records were identical, the better poll rank going
  in (two S1 championships).
- "seed": World Championship / playoff games. A single game (S1) is hosted by
  the higher seed; a two-legged tie (S2) by the worse seed in leg 1 and the
  better seed in leg 2, as now (per the user).
"""
import re
from collections import Counter, defaultdict

import openpyxl

NUMBER_WORDS = {w: i for i, w in enumerate(
    "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN".split(), start=1)}
REGION_NAME_FIX = {"Lily Valley": "LilyValley"}
GOALS = re.compile(r'^\[(\d+)-(\d+)\]$')
DSCR = re.compile(r'^\{(-?[\d.]+)-(-?[\d.]+)\}$')
CUPS = {"Crystal": "CRY", "Emerald": "EME", "Platinum": "PLA", "Gray": "GRA",
        "Prism": "PRI", "Kahuna": "KAH", "Gigantamax": "GIG"}


def _week(v):
    """A week label from the Elo workbook's archives ("WEEK 3", "W3"); anything
    else in that column (the side panel's numbers, a ditto mark) is not one."""
    if isinstance(v, str):
        v = v.strip().replace("WEEK ", "W")
        if re.match(r'^W\d+$', v):
            return v
    return None


def read_elo(ewb, cfg):
    """{team: {checkpoint: Elo}} and the checkpoint names in order (W0/START
    first)."""
    rows = list(ewb[cfg["elo_sheet"]].iter_rows(values_only=True))
    hdr = rows[0]
    cols = [(j, h) for j, h in enumerate(hdr) if j >= 1 and isinstance(h, str)
            and (h == "START" or re.match(r'^W\d+$', h))]
    elo = {}
    for r in rows[1:]:
        if isinstance(r[0], str) and r[0].strip():
            elo[r[0]] = {h: r[j] for j, h in cols}
    return elo, [h for _, h in cols]


def read_teams(wb, ewb, cfg):
    c = cfg["rankings"]
    elo, checkpoints = read_elo(ewb, cfg)
    teams = {}
    for rownum, r in enumerate(wb[c["sheet"]].iter_rows(min_row=2, values_only=True), 2):
        if len(r) <= c["name"] or not isinstance(r[c["name"]], str) or not isinstance(r[c["region"]], str):
            continue
        name = r[c["name"]]
        if c.get("checkpoint_name") is not None and r[c["checkpoint_name"]] != name:
            raise ValueError(f"{c['sheet']} row {rownum}: rank checkpoints are keyed to "
                             f"{r[c['checkpoint_name']]!r}, not {name!r}")
        region = r[c["region"]]
        dex = r[c["dex"]]
        teams[dex] = {
            "dex": dex, "name": name, "region": REGION_NAME_FIX.get(region, region),
            "division": r[c["division"]], "tier": "", "primary_type": "", "basclm": "",
            "opening_rank": r[c["opening_rank"]] if c.get("opening_rank") is not None else None,
            "sheet_opening_elo": elo[name][checkpoints[0]],
            "final_rank": r[c["final_rank"]], "final_ovr": r[c["ovr"]],
            "final_elo": elo[name][checkpoints[-1]],
            "accolades": "",
            "record": {k: r[i] for k, i in cfg["record_cols"].items()},
            "seed": r[c["seed"]] if c.get("seed") is not None else None,
        }
    if set(elo) != {t["name"] for t in teams.values()}:
        raise ValueError(f"{cfg['elo_sheet']}: teams differ from {c['sheet']}")
    return teams


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def s3_event_code(label):
    """S3's Archive labels -> (code, is an event label). Anything else written
    in the label column (the changelog notes, the Rivalry Week derby names) is
    a note on the current event."""
    t = label.strip()
    leg = re.search(r' - (1st|2nd) Leg$', t)
    leg_n = {"1st": 1, "2nd": 2}[leg.group(1)] if leg else None
    base = t[:leg.start()] if leg else t
    sfx = f"-{leg_n}" if leg_n else ""
    m = re.match(r'^Matchday (\w+)$', base)
    if m:
        return f"MD{NUMBER_WORDS[m.group(1).upper()]}"
    m = re.match(r'^WCS (First|Second|Third|Fourth|Fifth) Round$', base)
    if m:
        return f"WCS-R{['First', 'Second', 'Third', 'Fourth', 'Fifth'].index(m.group(1)) + 1}{sfx}"
    stage = {"Round of 16": "R16", "Round of 20": "R20", "Quarterfinal": "QF",
             "Semifinal": "SF", "Championship": "F", "Final": "F", "First Round": "R1",
             "Second Round": "R2"}
    m = re.match(r'^(WCS|MRL|UTL) (Round of 16|Round of 20|Quarterfinal|Semifinal|Championship)$', base)
    if m:
        return f"{m.group(1)}-{stage[m.group(2)]}{sfx}"
    m = re.match(r'^(MRL|UTL) Group ([A-H]) - Match (\d)$', base)
    if m:
        return f"{m.group(1)}-{m.group(2)}{m.group(3)}{sfx}"
    m = re.match(r'^(\w+) Cup (First Round|Second Round|Quarterfinal|Semifinal|Final)$', base)
    if m and m.group(1) in CUPS:
        return f"{CUPS[m.group(1)]}-{stage[m.group(2)]}{sfx}"
    if re.match(r'^[\w ]+ Conference Championship$', base):
        return "CC-F"
    m = re.match(r'^Play-[Ii]n Round - Match ([XYZ])$', base)
    if m:
        return f"WC-PI{m.group(1)}{sfx}"
    m = re.match(r'^(Opening Round|Winners - (Quarterfinal|Semifinal|Final)|'
                 r'Lower - (First Round|Second Round|Quarterfinal|Semifinal|Final))$', base)
    if m:
        part = {"Opening Round": "OR"}.get(base) or (
            ("W" if base.startswith("Winners") else "L")
            + {"Quarterfinal": "QF", "Semifinal": "SF", "Final": "F",
               "First Round": "R1", "Second Round": "R2"}[base.split(" - ")[1]])
        return f"WC-{part}{sfx}"
    m = re.match(r'^GRAND FINAL( RESET)? - GAME (ONE|TWO|THREE)$', base)
    if m:
        return f"WC-GF{'R' if m.group(1) else ''}-{NUMBER_WORDS[m.group(2)]}"
    return None


def _new_event(events, code, title, rownum, sub=None):
    e = {"code": code, "title": title, "sub": sub, "row": rownum, "games": 0,
         "notes": [], "dates": []}
    events.append(e)
    return e


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------

def _elo_weeks(ewb, cfg, keys):
    """{game key: week} from the Elo workbook's own copy of the archive. It
    holds the same games (some blocks reordered by week); winner, score, loser
    and goals identify each one."""
    a = cfg["elo_archive"]
    out, wk = {}, None
    for r in ewb[a["sheet"]].iter_rows(min_row=2, values_only=True):
        w = _week(r[a["week"]]) if len(r) > a["week"] else None
        if w:
            wk = w
        if len(r) > a["loser"] and isinstance(r[a["winner"]], str) and r[a["winner"]].strip():
            k = (r[a["winner"]], r[a["score_w"]], r[a["score_l"]], r[a["loser"]], r[a["goals"]])
            if k in out:
                raise ValueError(f"{a['sheet']}: game {k} listed twice")
            out[k] = wk
    if Counter(keys) != Counter(out.keys()):
        raise ValueError(f"{a['sheet']}: not the same games as the main workbook's archive")
    return out


def read_games(wb, ewb, cfg, teams):
    """Every game, in Archive order, as extract_history's game dicts."""
    cols = cfg["archive_cols"]
    name_to_dex = {t["name"]: d for d, t in teams.items()}
    region_of = {t["name"]: t["region"] for t in teams.values()}
    elo, checkpoints = read_elo(ewb, cfg)
    overrides = cfg.get("label_overrides", {})
    raw = []
    for rownum, r in enumerate(wb[cfg["archive"]].iter_rows(min_row=1, values_only=True), 1):
        if rownum == 1 or len(r) <= cols["loser"]:
            continue
        if isinstance(r[cols["winner"]], str) and r[cols["winner"]].strip():
            raw.append((rownum, r))
    def key(r):
        return (r[cols["winner"]], r[cols["score_w"]], r[cols["score_l"]], r[cols["loser"]],
                r[cols["goals"]])
    weeks = _elo_weeks(ewb, cfg, [key(r) for _, r in raw])

    events, games = [], []
    by_code = {}
    cur = None
    day = None
    week_events = cfg.get("week_events")
    fixes = cfg.get("cell_corrections", {})
    for rownum, r in raw:
        week = weeks[key(r)]
        if rownum in fixes:
            # A cell corrected as the row is read (row: column, the sheet's
            # value, the corrected value, the reason).
            field, sheet, fixed, _why = fixes[rownum]
            if r[cols[field]] != sheet:
                raise SystemExit(f"cell correction at {cfg['archive']} row {rownum}: expected "
                                 f"{field}={sheet!r}, sheet has {r[cols[field]]!r}")
            r = list(r)
            r[cols[field]] = fixed
        winner, loser = r[cols["winner"]], r[cols["loser"]]
        if cols.get("date") is not None:
            d = r[cols["date"]]
            if hasattr(d, "strftime"):
                day = d.strftime("%Y-%m-%d")
            elif isinstance(d, str) and d.strip():
                day = d.strip()
        label = r[cols["label"]] if cols.get("label") is not None else None
        if rownum in overrides:
            label, note = overrides[rownum]
            if note and cur is not None:
                cur["notes"].append(note)
        if week_events:
            # S1/S2: events are the weeks of the Elo workbook.
            code, title = week_events[week]
            if code not in by_code:
                by_code[code] = _new_event(events, code, title, rownum)
            target = by_code[code]
        else:
            target = cur
            if isinstance(label, str) and label.strip():
                code = s3_event_code(label)
                if code is None:
                    cur["notes"].append(label.strip())
                else:
                    if code not in by_code or (events and events[-1]["code"] != code
                                               and code == "CC-F"):
                        by_code[code] = _new_event(events, code, label.strip(), rownum)
                    target = by_code[code]
                    cur = target
        target["games"] += 1
        if day and day not in target["dates"]:
            target["dates"].append(day)

        sw, sl = r[cols["score_w"]], r[cols["score_l"]]
        spread = r[cols["spread"]] if cols.get("spread") is not None else None
        interest = r[cols["interest"]] if cols.get("interest") is not None else None
        dscr = r[cols["dscr"]] if cols.get("dscr") is not None else None
        pair = DSCR.match(str(dscr)) if dscr is not None else None
        if dscr is not None and not pair:
            raise ValueError(f"{cfg['archive']} row {rownum}: unparseable DSCR {dscr!r}")
        sp = f"{round(float(spread), 2):g}" if spread is not None else ""
        nsp = f"{-round(float(spread), 2):g}" if spread is not None else ""
        gi = str(round(float(interest))) if interest is not None else ""
        i = checkpoints.index(week)
        ew, el = elo[winner][week], elo[loser][week]
        k = ew - elo[winner][checkpoints[i - 1]]
        w = ("3", "?", str(name_to_dex[loser]), str(sw), str(sl), "", sp, gi,
             pair.group(1) if pair else "", str(ew))
        l = ("0", "?", str(name_to_dex[winner]), str(sl), str(sw), "", nsp, gi,
             pair.group(2) if pair else "", str(el))
        ha = r[cols["ha"]] if cols.get("ha") is not None else None
        rank_w = r[cols["rank_w"]] if cols.get("rank_w") is not None else None
        rank_l = r[cols["rank_l"]] if cols.get("rank_l") is not None else None
        if cfg.get("label_ranks") and week in cfg["label_ranks"] and isinstance(label, str):
            m = re.match(r'^\s*(#\d+|NR)\.? v\.? (#\d+|NR)\s*$', label)
            if m:
                rank_w, rank_l = (None if x == "NR" else int(x[1:]) for x in m.groups())
        note = r[cols["note"]] or ""
        games.append({
            "row": rownum, "event": target, "week": week, "winner_name": winner,
            "loser_name": loser,
            "home_away": str(ha).strip().upper() if ha else "",
            "host_source": "recorded" if ha else "",
            "w": w, "l": l, "short": True, "raw_w": "", "raw_l": "",
            "string_source": "archive", "ex": None,
            "mark": (r[cols["mark"]] or "") if cols.get("mark") is not None else "",
            "note": note if isinstance(note, str) else str(note), "day": day or "",
            "rank_w": rank_w, "rank_l": rank_l, "elo_k": k,
            "goals": r[cols["goals"]],
            "col": {"score": (sw, sl), "spread": spread, "interest": interest, "dscr": dscr},
        })

    seen = Counter()
    for n, e in enumerate(events, start=1):
        e["round"] = n
        seen[e["code"]] += 1
        if seen[e["code"]] > 1:
            e["code"] = f"{e['code']}.{seen[e['code']]}"

    # Game type from the event, as in S4.
    for g in games:
        code = g["event"]["code"]
        gtype = next((t for rx, t in cfg["type_by_code"] if re.match(rx, code)), None)
        if gtype is None:
            raise ValueError(f"no game type rule for event {code}")
        if gtype == "R/I":
            gtype = "R" if region_of[g["winner_name"]] == region_of[g["loser_name"]] else "I"
        g["w"] = (g["w"][0], gtype) + tuple(g["w"][2:])
        g["l"] = (g["l"][0], gtype) + tuple(g["l"][2:])

    # Ties decided in aggregate overtime. The row holds the leg's regulation
    # score; its note ("AggOT - 29[15-15], Resort Area wins 36-33[5-3] on
    # agg.") the level regulation aggregate and the aggregate after AggOT. The
    # AggOT points go in aggot_a/b; per the user they are not statistics.
    pattern = re.compile(r'^AggOT - (\d+)\[[^]]*\], (.+?) wins (\d+)-(\d+)')
    for g in games:
        m = pattern.match(g["note"])
        if g["row"] in cfg.get("aggot_rows", {}):
            if not m:
                raise ValueError(f"row {g['row']}: listed as AggOT, note reads {g['note']!r}")
            level, who, agg_win, agg_lose = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
            who = re.sub(r'^\d+-seed ', '', who)   # S2: "3-seed Rustboro"
            pair = (g["winner_name"], g["loser_name"])
            winners = [t for t in pair if t.startswith(who) or who.startswith(t)]
            if len(winners) != 1:
                raise ValueError(f"row {g['row']}: AggOT winner {who!r} is neither side")
            prefix = g["event"]["code"].rsplit("-", 1)[0]
            reg = Counter()
            for x in games:
                if {x["winner_name"], x["loser_name"]} == set(pair) and \
                        x["event"]["code"].rsplit("-", 1)[0] == prefix:
                    reg[x["winner_name"]] += int(x["w"][3])
                    reg[x["loser_name"]] += int(x["l"][3])
            if not (reg[pair[0]] == reg[pair[1]] == level):
                raise ValueError(f"row {g['row']}: AggOT note says level at {level}, the legs "
                                 f"give {dict(reg)}")
            final = {winners[0]: agg_win, (set(pair) - set(winners)).pop(): agg_lose}
            g["aggot"] = (final[pair[0]] - level, final[pair[1]] - level)
            g["aggot_winner"] = winners[0]
        elif m:
            raise ValueError(f"row {g['row']}: an AggOT note not listed in aggot_rows")
    return events, games


# ---------------------------------------------------------------------------
# Hosts for S1 and S2
# ---------------------------------------------------------------------------

def read_template(wb):
    """S2 Divisions: the seven division rounds (G15:M18, "x=y") and the letter
    each round is tagged with (G14:M14: 1-A 2-C 3-B 4-X 5-B 6-C 7-A)."""
    ws = wb["S2 Divisions"]
    rows = list(ws.iter_rows(min_row=14, max_row=18, values_only=True))
    letters = [str(v).split("-")[1] for v in rows[0][6:13]]
    rounds = []
    for j in range(6, 13):
        rounds.append([tuple(int(x) for x in str(rows[i][j]).split("=")) for i in range(1, 5)])
    return rounds, letters


def s2_positions(wb, teams):
    rows = list(wb["S2 Divisions"].iter_rows(min_row=3, max_row=10, values_only=True))
    pos = {}
    for c in (1, 2, 4, 5, 7, 8, 10, 11, 13, 14, 16, 17):
        for i in range(8):
            pos[rows[i][c]] = i + 1
    return pos


def s1_positions(wb, teams):
    """Each division's teams in the order of their S1 Rankings codes (column A:
    AA, AB, ... -- the order of a per-team sheet's columns)."""
    groups = defaultdict(list)
    for d, t in teams.items():
        groups[(t["region"], t["division"])].append(t["name"])
    code = {t["name"]: t["dex"] for t in teams.values()}
    pos = {}
    for names in groups.values():
        for i, n in enumerate(sorted(names, key=lambda n: (len(code[n]), code[n])), start=1):
            pos[n] = i
    return pos


def schedule_hosts(wb, cfg, teams, games):
    """{Archive row: (host name, source)} for every S1/S2 game."""
    s = cfg["schedule"]
    rounds, letters = read_template(wb)
    pos = (s1_positions if s["positions"] == "s1" else s2_positions)(wb, teams)
    t_of = {t["name"]: t for t in teams.values()}
    first_hosts = s["orientation"] == "first"
    hosts, errors = {}, []

    used = defaultdict(set)     # (week, group) -> template rounds its games came from

    def template_host(a, b, allowed, week, group):
        pa, pb = pos[a], pos[b]
        for ri in allowed:
            for x, y in rounds[ri]:
                if {x, y} == {pa, pb} and pa != pb:
                    used[(week, group)].add(ri)
                    hx = x if first_hosts else y
                    return a if pa == hx else b
        return None

    interc = {}
    if s.get("interc"):
        sheet, lo, hi, team_col, spec = s["interc"]
        for r in wb[sheet].iter_rows(min_row=lo, max_row=hi, values_only=True):
            for wk, (col, role) in spec.items():
                interc[(wk, frozenset((r[team_col], r[col])))] = (
                    r[team_col] if role == "home" else r[col])

    regular = [g for g in games if g["week"] in s["regular_weeks"]]
    for g in regular:
        a, b = g["winner_name"], g["loser_name"]
        ta, tb = t_of[a], t_of[b]
        if ta["region"] == tb["region"] and ta["division"] == tb["division"]:
            h = template_host(a, b, range(7), g["week"], ("div", ta["region"], ta["division"]))
        elif ta["region"] == tb["region"]:
            h = template_host(a, b, [i for i, L in enumerate(letters) if L == s["crossover"]],
                              g["week"], ("cross", ta["region"]))
        elif s.get("interc"):
            h = interc.get((int(g["week"][1:]), frozenset((a, b))))
            if h is not None:
                hosts[g["row"]] = (h, "interc")
                continue
        else:
            h = template_host(a, b, [i for i, L in enumerate(letters) if L == "X"],
                              g["week"], ("inter", frozenset((ta["region"], tb["region"]))))
        if h is None:
            errors.append(f"row {g['row']}: {a} v {b} ({g['week']}) fits no schedule pairing")
        else:
            hosts[g["row"]] = (h, "schedule")

    # The positions are only proved by the weeks: in any week a division's
    # games (or a conference's crossovers) must all come from ONE template
    # round, and a division plays each of its seven rounds in a different week.
    # (Every pair sits in some round, so without this any positions would do.)
    for (week, group), rs in sorted(used.items(), key=str):
        if len(rs) != 1:
            errors.append(f"{week}: {group[0]} games of {'/'.join(map(str, group[1:]))} mix "
                          f"template rounds {sorted(r + 1 for r in rs)}")
    per_div = defaultdict(list)
    for (week, group), rs in used.items():
        if group[0] == "div":
            per_div[group].extend(rs)
    for group, rs in per_div.items():
        if sorted(set(rs)) != list(range(7)):
            errors.append(f"{'/'.join(group[1:])}: plays template rounds {sorted(set(r + 1 for r in rs))}")
        elif s["positions"] == "s1" and len(rs) != 7:
            errors.append(f"{'/'.join(group[1:])}: a template round spread over two weeks")

    # Conference championships: the better regular-season record hosts
    # (overall, then within the conference).
    rec = defaultdict(lambda: [0, 0, 0, 0])
    for g in regular:
        a, b = g["winner_name"], g["loser_name"]
        rec[a][0] += 1
        rec[b][1] += 1
        if t_of[a]["region"] == t_of[b]["region"]:
            rec[a][2] += 1
            rec[b][3] += 1
    for g in games:
        if g["event"]["code"] == "CC-F":
            a, b = g["winner_name"], g["loser_name"]
            ka = (rec[a][0] - rec[a][1], rec[a][2] - rec[a][3])
            kb = (rec[b][0] - rec[b][1], rec[b][2] - rec[b][3])
            if ka != kb:
                hosts[g["row"]] = (a if ka > kb else b, "record")
                continue
            # Identical records (two in S1, neither pair having met): the
            # better poll rank going into the game hosts (per the user).
            ra, rb = g["rank_w"], g["rank_l"]
            if ra == rb or (ra is None and rb is None):
                errors.append(f"row {g['row']}: conference championship {a} v {b} -- "
                              f"identical records and poll ranks, no host")
            else:
                better = a if rb is None or (ra is not None and ra < rb) else b
                hosts[g["row"]] = (better, "poll")
        elif g["week"] not in s["regular_weeks"]:
            sa, sb = t_of[g["winner_name"]]["seed"], t_of[g["loser_name"]]["seed"]
            if not isinstance(sa, int) or not isinstance(sb, int) or sa == sb:
                errors.append(f"row {g['row']}: no seeds for {g['winner_name']} v {g['loser_name']}")
            else:
                better, worse = (g["winner_name"], g["loser_name"]) if sa < sb else \
                    (g["loser_name"], g["winner_name"])
                # A two-legged tie (S2, codes ending -1/-2): the worse seed hosts
                # leg 1 and the better seed leg 2, as now; a single game (S1)
                # goes to the better seed.
                leg = re.search(r'-([12])$', g["event"]["code"])
                hosts[g["row"]] = (worse if leg and leg.group(1) == "1" else better, "seed")
    return hosts, errors, pos


def apply_hosts(hosts, games):
    for g in games:
        if g["row"] in hosts:
            h, src = hosts[g["row"]]
            g["home_away"] = "H" if h == g["winner_name"] else "A"
            g["host_source"] = src


# ---------------------------------------------------------------------------
# S3: the pod schedule
# ---------------------------------------------------------------------------

POD_INTRA = {"I1": {(1, 4): 1, (2, 3): 2},      # "4 at 1, 3 at 2"
             "I2": {(1, 3): 3, (2, 4): 4},      # "1 at 3, 2 at 4"
             "I3": {(1, 2): 1, (3, 4): 3}}      # "2 at 1, 4 at 3"
# Pod pairings: which pods meet, which side hosts, and the position pattern
# (0: 1v1 2v2 3v3 4v4; 1: 1v2 2v1 3v4 4v3).
POD_CROSS = {"SS1": ("same", "U", 0), "AC1": ("across", "R", 0), "DG1": ("diagonal", "L", 0),
             "SS2": ("same", "D", 1), "AC2": ("across", "L", 1), "DG2": ("diagonal", "R", 1)}


def read_pods(wb):
    """S3 Divisions: each conference's four pods laid out two by two (upper /
    lower rows, left / right columns), each pod's teams in position order."""
    rows = list(wb["Divisions"].iter_rows(min_row=1, max_row=25, values_only=True))

    def cell(r, c):
        row = rows[r - 1]
        return row[c] if c < len(row) else None
    pods = {}
    for hr in (2, 14):
        for c in range(len(rows[hr - 1])):
            v = cell(hr, c)
            if not (isinstance(v, str) and v.endswith(" Conference")):
                continue
            conf = REGION_NAME_FIX.get(v[:-len(" Conference")], v[:-len(" Conference")])
            for ud, pr in (("U", hr + 1), ("D", hr + 6)):
                for lr, pc in (("L", c), ("R", c + 4)):
                    for i in range(4):
                        pods[cell(pr + 1 + i, pc)] = dict(conf=conf, pod=cell(pr, pc), ud=ud,
                                                          lr=lr, pos=i + 1)
    return pods


def pod_hosts(wb, cfg, teams, games):
    """S3's conference games against its pod schedule (the Divisions sheet,
    odd-season version, per the user): a recorded host must agree with it, and
    a game with none takes the schedule's. Returns (filled rows, errors)."""
    pods = read_pods(wb)
    names = {t["name"] for t in teams.values()}
    if set(pods) != names:
        return 0, [f"Divisions: pod teams differ from Rankings ({sorted(set(pods) ^ names)[:4]})"]
    kinds = cfg["pod_schedule"]
    filled, errors = 0, []
    for g in games:
        kind = kinds.get(g["event"]["code"])
        if kind is None or g["w"][1] != "R":
            continue
        a, b = g["winner_name"], g["loser_name"]
        pa, pb = pods[a], pods[b]
        host = None
        if kind in POD_INTRA:
            if pa["pod"] == pb["pod"]:
                h = POD_INTRA[kind].get(tuple(sorted((pa["pos"], pb["pos"]))))
                host = None if h is None else (a if pa["pos"] == h else b)
        else:
            rel, side, pattern = POD_CROSS[kind]
            same_lr, same_ud = pa["lr"] == pb["lr"], pa["ud"] == pb["ud"]
            meets = {"same": same_lr and not same_ud, "across": same_ud and not same_lr,
                     "diagonal": not same_lr and not same_ud}[rel]
            fits = (pa["pos"] == pb["pos"]) if pattern == 0 else \
                ({pa["pos"], pb["pos"]} in ({1, 2}, {3, 4}))
            if meets and fits:
                key = "ud" if side in "UD" else "lr"
                host = a if pa[key] == side else b
        if host is None:
            errors.append(f"row {g['row']}: {a} v {b} ({g['event']['code']}) fits no pod pairing "
                          f"for {kind}")
        elif g["home_away"]:
            recorded = a if g["home_away"] == "H" else b
            if recorded != host:
                errors.append(f"row {g['row']}: recorded host {recorded}, pod schedule says {host}")
        else:
            g["home_away"] = "H" if host == a else "A"
            g["host_source"] = "schedule"
            filled += 1
    return filled, errors
