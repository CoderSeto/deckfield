#!/usr/bin/env python3
"""
Freeze a finished season's dashboard as its permanent, numbers-only record.

    python3 freeze_season.py 9

Writes three things, and refuses to overwrite any of them:

- deckfield_dashboard_s<N>.html -- a byte-for-byte copy of
  deckfield_dashboard.html. Every tab on it is drawn from inline JSON, so it
  needs no engine and no database, and regenerate_dashboard.py never touches
  it.
- archive/s<N>_final.json -- every data constant on that page, as plain JSON,
  minus TEAMS_EXPORT_TSV (its Secondary Type column is random on every
  export) and NEXT_MATCHDAY_DATA (it drives the game, it is not history).
- archive/SHA256SUMS -- one line per frozen file, `sha256sum -c` format.
  regenerate_dashboard.py checks it before doing anything, so a frozen
  season cannot change by accident.

Why freeze at all: the season's numbers come from the engine's formulas,
and the next season will change the engine. Rebuilding a finished season
later from the workbook plus results/ is an audit, not the record -- if the
two ever disagree, the archive wins and the difference is the engine having
moved.

JSON-valued constants are copied from their exact text (parsed, so the file
is valid JSON, but every number is the one the page holds). The few that are
JavaScript rather than JSON (REGION_ORDER's single quotes, RT_MD_LABELS'
bare numeric keys, the two lookups the page computes from DATA) are
evaluated in Node, which is what the page itself does.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "deckfield_dashboard.html")
ARCHIVE_DIR = os.path.join(HERE, "archive")
SUMS_PATH = os.path.join(ARCHIVE_DIR, "SHA256SUMS")

EXCLUDED = {
    "TEAMS_EXPORT_TSV": "Secondary Type is a fresh random 1-18 on every export",
    "NEXT_MATCHDAY_DATA": "drives the next game; not part of the season's record",
}

# Each top-level `const NAME = ...;` runs from its declaration to the first
# line that ends in `;` (RT_MD_LABELS spans two lines; every other one, one).
CONST_RE = re.compile(r'^const ([A-Z][A-Z0-9_]*) = (.*?);[ \t]*$', re.M | re.S)


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def extract_constants(html):
    raw = [(m.group(1), m.group(2)) for m in CONST_RE.finditer(html)]
    names = [n for n, _ in raw]
    if len(names) != len(set(names)):
        raise RuntimeError(f"duplicate constant names: {names}")

    values, needs_js = {}, []
    for name, body in raw:
        try:
            values[name] = json.loads(body)
        except json.JSONDecodeError:
            needs_js.append(name)

    if needs_js:
        # Evaluate every declaration in page order (the computed lookups read
        # DATA), then hand back only the ones JSON could not parse.
        script = "".join(f"const {n} = {b};\n" for n, b in raw)
        script += "process.stdout.write(JSON.stringify({%s}));\n" % ", ".join(needs_js)
        out = subprocess.run(["node", "-"], input=script, capture_output=True,
                             text=True, check=True).stdout
        values.update(json.loads(out))

    return names, values


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("usage: python3 freeze_season.py <season>")
    season = int(sys.argv[1])

    frozen_html = os.path.join(HERE, f"deckfield_dashboard_s{season}.html")
    frozen_json = os.path.join(ARCHIVE_DIR, f"s{season}_final.json")
    for p in (frozen_html, frozen_json):
        if os.path.exists(p):
            sys.exit(f"refusing to overwrite {os.path.relpath(p, HERE)} -- a frozen season stays frozen")

    with open(SOURCE, "rb") as f:
        html_bytes = f.read()
    html = html_bytes.decode("utf-8")

    names, values = extract_constants(html)
    data = values["DATA"]
    if data.get("season") != season:
        sys.exit(f"dashboard is season {data.get('season')}, not {season}")

    subtitle = re.search(r'<div class="subtitle">Season (\d+) &mdash; through round (\d+)</div>', html)
    through_round = int(subtitle.group(2)) if subtitle else data.get("round")

    kept = [n for n in names if n not in EXCLUDED]
    meta = {
        "season": season,
        "through_round": through_round,
        "frozen_on": datetime.date.today().isoformat(),
        "source": "deckfield_dashboard.html",
        "source_sha256": hashlib.sha256(html_bytes).hexdigest(),
        "frozen_dashboard": os.path.basename(frozen_html),
        "constants": kept,
        "excluded": EXCLUDED,
        "note": ("Numbers only. Each key under `data` is the dashboard constant of "
                 "the same name, exactly as published. This file, not a rebuild, "
                 "is the season's record."),
    }

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(frozen_html, "wb") as f:
        f.write(html_bytes)

    # One constant per line: valid JSON, and the top level stays readable
    # without pretty-printing ~1 MB of tables.
    lines = [f"  {json.dumps(n)}: {json.dumps(values[n], ensure_ascii=False)}" for n in kept]
    with open(frozen_json, "w", encoding="utf-8") as f:
        f.write('{\n "meta": ' + json.dumps(meta, ensure_ascii=False) + ',\n "data": {\n')
        f.write(",\n".join(lines))
        f.write("\n }\n}\n")

    # Prove the file reads back to exactly the page's values before trusting it.
    with open(frozen_json, encoding="utf-8") as f:
        reread = json.load(f)["data"]
    if reread != {n: values[n] for n in kept}:
        raise RuntimeError("archive JSON does not read back identically to the dashboard's constants")

    with open(SUMS_PATH, "a") as f:
        for p in (frozen_html, frozen_json):
            f.write(f"{sha256(p)}  {os.path.relpath(p, HERE)}\n")

    print(f"Froze season {season} through round {through_round}:")
    print(f"  {os.path.relpath(frozen_html, HERE)}")
    print(f"  {os.path.relpath(frozen_json, HERE)}  ({len(kept)} constants)")
    print(f"  checksums appended to {os.path.relpath(SUMS_PATH, HERE)}")


if __name__ == "__main__":
    main()
