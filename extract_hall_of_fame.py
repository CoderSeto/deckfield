"""One-shot extraction of the workbook's HOF sheet into hall_of_fame.json.

Seasons 1-8 are history and never change, so they live in a static file the
engine reads (the same arrangement as rank_history_verbatim.json); Season 9
onward is derived from real results by deckfield_ratings.hall_of_fame().

Each name keeps the REGION IT HELD AT THE TIME, read from the cell's font
colour -- the sheet colours every name by region, and a team that has since
moved (Olivine City*, an Indigo team in S3) must not be repainted in today's
colour. Unknown colours fall back to the team's current region.

    python3 extract_hall_of_fame.py "Baccer Game S9 Stats.xlsm"
"""
import json
import sys

import openpyxl

import deckfield_ratings as db

FONT_RGB_REGION = {
    "FFFF0000": "Indigo", "FFE26B0A": "Delta", "FF7030A0": "Kalosite",
    "FF00B0F0": "Vertress", "FFFF3399": "Lanakila", "FF002060": "Dynamax",
    "FF00B050": "LilyValley", "FF9B7B65": "Phoenix",
}
FONT_THEME_REGION = {2: "Silver", 5: "Delta", 7: "Terastal"}

# Teams listed under their CURRENT name, with a note naming the old one (per
# explicit instruction, 2026-10-05: "call it Battle Zone with a note that it
# was Resort Area").
RENAMED = {"Resort Area": "Battle Zone"}

# Which header columns make up each title plaque in the top block.
TITLE_PLAQUES = [
    ("world", "World Championship", ["B", "C"]),
    ("cup", "Cup Championship", ["E", "F"]),
    ("rds", "Regional Cups", ["H", "I", "J"]),
    ("league", "League Championship", ["L", "M", "N", "O"]),
]


def main(path):
    conn = db.get_connection()
    current = {r["name"]: r["region"] for r in conn.execute("SELECT name, region FROM teams")}
    conn.close()
    display_to_internal = {db.region_display_name(r): r for r in db.REGION_COLORS}

    ws = openpyxl.load_workbook(path, data_only=True)["HOF"]

    def team(cell):
        v = cell.value
        if not isinstance(v, str) or v.strip() in ("", "-"):
            return None
        name = v.strip()
        col = cell.font.color if cell.font else None
        region = None
        if col is not None and col.type == "rgb":
            region = FONT_RGB_REGION.get(col.rgb)
        elif col is not None and col.type == "theme":
            region = FONT_THEME_REGION.get(col.theme)
        region = region or current.get(name.rstrip("*"))
        out = {"name": name, "region": region}
        if name in RENAMED:
            out = {"name": RENAMED[name], "region": region, "note": f"Formerly {name}"}
        elif name.endswith("*"):
            out["note"] = "Since renamed or moved conference"
        return out

    out = {"plaques": {k: {"title": t, "seasons": {}} for k, t, _ in TITLE_PLAQUES},
           "regions": {}, "placings": {}}
    header, block = {}, None
    for row in ws.iter_rows():
        a = row[0].value
        cells = {c.column_letter: c for c in row}
        if not (isinstance(a, str) and a.startswith("Season")):
            labels = {k: c.value.strip() for k, c in cells.items()
                      if k != "A" and isinstance(c.value, str) and c.value.strip()}
            if not labels:
                continue
            if any(v.startswith("No.") for v in labels.values()):
                block = "placings"
            elif any(v.endswith("Champion") and v[:-9].strip() in display_to_internal
                     for v in labels.values()):
                block = "regions"
            else:
                block = "titles"
            # A title-block header row only restates the columns it changes.
            # "2* Div." in the sheet means 2nd Division.
            labels = {k: v.replace("2* Div.", "2nd Div.") for k, v in labels.items()}
            header = {**header, **labels} if block == "titles" else labels
            continue
        season = str(db_season_number(a))
        if block == "titles":
            for key, _title, cols in TITLE_PLAQUES:
                teams = [team(cells[c]) for c in cols]
                if any(teams):
                    out["plaques"][key]["seasons"][season] = {
                        "labels": [header.get(c, "") for c in cols], "teams": teams}
        elif block == "regions":
            for col, label in header.items():
                region = display_to_internal[label[:-9].strip()]
                t = team(cells[col])
                if t:
                    out["regions"].setdefault(region, {})[season] = t
        elif block == "placings":
            places = [team(cells[c]) for c in sorted(header, key=lambda c: openpyxl.utils.column_index_from_string(c))]
            if any(places):
                out["placings"][season] = places

    with open("hall_of_fame.json", "w") as f:
        json.dump(out, f, indent=1)
    print("wrote hall_of_fame.json")


WORDS = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve"]


def db_season_number(label):
    return WORDS.index(label.split()[1]) + 1


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Baccer Game S9 Stats.xlsm")
