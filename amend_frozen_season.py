#!/usr/bin/env python3
"""
Amend a frozen season's record -- ONLY on the user's explicit instruction.

    DECKFIELD_SEASON=9 DECKFIELD_DB=<the corrected rebuild> \\
        python3 amend_frozen_season.py 9 DATA,STRENGTH_DATA,RANK_ELO_HISTORY "<reason>"

A frozen season is never regenerated (see CLAUDE.md, "Season 9 is frozen"):
regenerate_dashboard.py refuses to run if a frozen file changes, and the rule
for an accidental change is to restore it from git. This script is the one
deliberate exception, for a correction the user has asked for. It changes
nothing but what it is told to, and records why:

1. Rebuilds the named constants from the database (which must be the
   corrected rebuild of the season, run AS that season) by regenerating a
   scratch copy of the current dashboard.
2. Writes them into the frozen page (deckfield_dashboard_s<N>.html) in place
   of the old ones, plus its roster export (TEAMS_EXPORT_TSV) rebuilt from the
   same database with each team's frozen Secondary Type kept -- every other
   byte of the page is untouched.
3. Rewrites archive/s<N>_final.json from the amended page and
   archive/s<N>_inputs.json from the database (cross-checked against the
   amended record), each with an `amended` entry in its meta naming the date,
   the reason, the constants and the previous checksum.
4. Rewrites those three files' lines in archive/SHA256SUMS.

The previous version stays in git history.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUMS = os.path.join(HERE, "archive", "SHA256SUMS")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def const_line(content, name):
    m = re.search(rf'^const {name} = .*;\n', content, re.M)
    if m is None:
        raise SystemExit(f"no `const {name}` line found")
    return m


def main():
    if len(sys.argv) != 4 or not sys.argv[1].isdigit():
        sys.exit("usage: python3 amend_frozen_season.py <season> <CONST,CONST,...> <reason>")
    season, names, reason = int(sys.argv[1]), sys.argv[2].split(","), sys.argv[3]
    import deckfield_ratings as db
    import freeze_season
    import freeze_season_inputs
    if db.CURRENT_SEASON != season:
        sys.exit(f"run as season {season} (DECKFIELD_SEASON={season}) against that season's corrected database")

    page_path = os.path.join(HERE, f"deckfield_dashboard_s{season}.html")
    final_path = os.path.join(HERE, "archive", f"s{season}_final.json")
    inputs_path = os.path.join(HERE, "archive", f"s{season}_inputs.json")
    old = {p: sha(p) for p in (page_path, final_path, inputs_path)}
    today = datetime.date.today().isoformat()
    note = {"on": today, "reason": reason, "constants": names + ["TEAMS_EXPORT_TSV"]}

    # 1. the corrected constants, from a scratch regenerate of the current dashboard
    with tempfile.TemporaryDirectory() as tmp:
        scratch = os.path.join(tmp, "dash.html")
        shutil.copy(os.path.join(HERE, "deckfield_dashboard.html"), scratch)
        subprocess.run([sys.executable, os.path.join(HERE, "regenerate_dashboard.py"), scratch],
                       check=True, capture_output=True)
        fresh = open(scratch, encoding="utf-8").read()

    # 2. into the frozen page
    page = open(page_path, encoding="utf-8").read()
    for name in names:
        new_line = const_line(fresh, name).group(0)
        m = const_line(page, name)
        page = page[:m.start()] + new_line + page[m.end():]
    m = re.search(r'^const TEAMS_EXPORT_TSV = ("(?:[^"\\]|\\.)*");\n', page, re.M)
    head, *rows = json.loads(m.group(1)).split("\n")
    cols = head.split("\t")
    secondary = {r.split("\t")[1]: r.split("\t")[cols.index("Secondary Type")] for r in rows}
    _, tsv = db.export_teams_for_deckfield(season)
    new_head, *new_rows = tsv.split("\n")
    if new_head != head:
        raise SystemExit("roster header differs from the frozen one")
    kept = []
    for r in new_rows:
        f = r.split("\t")
        f[cols.index("Secondary Type")] = secondary[f[1]]
        kept.append("\t".join(f))
    tsv_line = "const TEAMS_EXPORT_TSV = " + json.dumps("\n".join([head] + kept)) + ";\n"
    page = page[:m.start()] + tsv_line + page[m.end():]
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(page)

    # 3a. archive/s<N>_final.json from the amended page
    final = json.load(open(final_path, encoding="utf-8"))
    names_all, values = freeze_season.extract_constants(page)
    kept_names = final["meta"]["constants"]
    meta = final["meta"]
    meta.setdefault("amended", []).append({**note, "previous_source_sha256": meta["source_sha256"],
                                           "previous_archive_sha256": old[final_path]})
    meta["source_sha256"] = sha(page_path)
    lines = [f"  {json.dumps(n)}: {json.dumps(values[n], ensure_ascii=False)}" for n in kept_names]
    with open(final_path, "w", encoding="utf-8") as f:
        f.write('{\n "meta": ' + json.dumps(meta, ensure_ascii=False) + ',\n "data": {\n')
        f.write(",\n".join(lines))
        f.write("\n }\n}\n")
    if json.load(open(final_path, encoding="utf-8"))["data"] != {n: values[n] for n in kept_names}:
        raise SystemExit("amended archive does not read back identically to the page")

    # 3b. archive/s<N>_inputs.json, cross-checked against the amended record
    db._FROZEN_CACHE.clear()
    payload = freeze_season_inputs.build_payload(season)
    previous = json.load(open(inputs_path, encoding="utf-8"))["meta"]
    payload["meta"]["frozen_on"] = previous["frozen_on"]
    payload["meta"]["amended"] = previous.get("amended", []) + [{**note, "previous_sha256": old[inputs_path]}]
    with open(inputs_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")

    # 4. the checksums
    out = []
    for line in open(SUMS).read().splitlines():
        digest, rel = line.split("  ", 1)
        path = os.path.join(HERE, rel)
        out.append(f"{sha(path) if path in old else digest}  {rel}")
    with open(SUMS, "w") as f:
        f.write("\n".join(out) + "\n")
    for p in old:
        print(f"amended {os.path.relpath(p, HERE)}: {old[p][:12]} -> {sha(p)[:12]}")


if __name__ == "__main__":
    main()
