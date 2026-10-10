#!/usr/bin/env python3
"""
Write a new season's fixed starting files from the previous season's FROZEN
record (SEASON10_PLAN section B, build step 2).

    python3 prepare_season.py 10

Reads only the frozen record -- archive/s<N-1>_final.json and
archive/s<N-1>_inputs.json -- plus legacy_points.json and conf_pods.json. It
never reads the workbook or recomputes the finished season. Writes, into
seasons/s<N>/:

    league_pods.json    the new divisions (B2): promotion/relegation applied to
                        the final division standings, seeded 1-16 by old
                        division then finishing position, in the usual pods
    start.json          each team's division, base climate (B5), EX seed (B6),
                        carryover seeds (B4), starting fatigue and Elo
    accolades.json      the stored accolade history entering the season (A1/A2)
    rds_seeds.json      Ribbon / Dream / Star seeding (B9)
    pa_draw_seeds.json  PA Draw seeding: previous final Elo, ties by OVR (B9)
    pa_process_seeds.json  PA Process seeding: the new division seeds end to end

These are the season's committed inputs. `deckfield_cli.py start-season`
builds the database from them; the schedule draw (draw_schedule.py) reads
league_pods.json. Running this again is a no-op when nothing has changed,
and it refuses to overwrite a file whose contents would differ (pass --force
only if a rule really changed before the season started).
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# B2: the existing pod layout, by seed.
POD_LAYOUT = {"UL": (1, 8, 9, 16), "UR": (2, 7, 10, 15), "LL": (4, 5, 12, 13), "LR": (3, 6, 11, 14)}
RDS_CUP_REGIONS = {
    "Ribbon": ("Indigo", "Silver", "Delta", "LilyValley"),
    "Dream": ("Vertress", "Phoenix", "Lanakila"),
    "Star": ("Kalosite", "Dynamax", "Terastal"),
}
EX_SCALE = 160          # B6: each of A and B is rescaled 0-160


def load(path):
    with open(os.path.join(HERE, path)) as f:
        return json.load(f)


def new_divisions(final, inputs):
    """{division: [(old_division, old_position, name), ...]} in seed order (B2)."""
    pr = final["PROMO_RELEGATION"]
    new = {}
    for div in range(1, 11):
        order = inputs["division_standings"][str(div)]
        for pos, name in enumerate(order, start=1):
            mark = pr[str(div)][pos - 1] or ""
            target = div - mark.count("P") + mark.count("R")
            new.setdefault(target, []).append((div, pos, name))
    for d, members in new.items():
        if len(members) != 16:
            raise SystemExit(f"new division {d} would hold {len(members)} teams, not 16")
    return {d: sorted(m) for d, m in sorted(new.items())}


def accolade_items(stored, earned, season):
    """[(title, [seasons])] from the stored raw text plus newly earned titles,
    in the order merge_accolades() would group them."""
    import deckfield_ratings as db
    items = []
    for entry in (stored or "").split("\n\n"):
        entry = " ".join(entry.split())
        if entry:
            items.append(db._split_accolade(entry))
    items += [(title, [season]) for title in earned]
    seasons_of, order = {}, []
    for title, seasons in items:
        if title not in seasons_of:
            seasons_of[title] = []
            order.append(title)
        seasons_of[title] += seasons
    return [(t, sorted(set(seasons_of[t]))) for t in order]


def raw_accolades(items):
    """The stored form merge_accolades() reads: one accolade per paragraph."""
    return "\n\n".join(f"{t} " + ", ".join(f"S{n}" for n in s) if s else t for t, s in items)


def build(season):
    import deckfield_ratings as db
    prev = season - 1
    final = db.frozen_season_data(prev)
    inputs = db.frozen_season_inputs(prev)
    legacy = load("legacy_points.json")["teams"]
    teams = inputs["teams"]                      # {dex: {...}}
    by_name = {t["name"]: int(dex) for dex, t in teams.items()}
    shown = {t["name"]: t for t in final["DATA"]["teams"]}
    region_display = {db.region_display_name(t["region"]) for t in teams.values()}

    # ---- B2: divisions and league pods
    divisions = new_divisions(final, inputs)
    seed_in_division = {}
    league_pods = {}
    for d, members in divisions.items():
        names = [n for _, _, n in members]
        for k, n in enumerate(names, start=1):
            seed_in_division[n] = (d, k)
        league_pods[str(d)] = {pod: {"pod_name": "-".join(map(str, seeds)),
                                     "teams": [names[s - 1] for s in seeds]}
                               for pod, seeds in POD_LAYOUT.items()}

    # ---- B6: EX seed = round(A + B + LegacyP)
    tots = [t["final_tot"] for t in teams.values()]
    ovrs = [t["final_ovr"] for t in teams.values()]
    tlo, thi, olo, ohi = min(tots), max(tots), min(ovrs), max(ovrs)

    def legacy_total(name):
        return sum(c["points"] for c in legacy.get(name, {}).values())

    # ---- A1/A2: accolades entering the season. The end-of-season roster already
    # carries the cup, RT and Division One titles (accolades_exported); the World
    # Championship titles were never exported, so they are added here.
    wc = final["HOF_DATA"]["plaques"]["world"]["seasons"][str(prev)]["teams"]
    wc_titles = {wc[0]["name"]: ["World Champion"], wc[1]["name"]: ["World Finalist"]}

    start, accolades = {}, {}
    for dex, t in sorted(teams.items(), key=lambda kv: int(kv[0])):
        name = t["name"]
        a = math.sqrt((t["final_tot"] - tlo) / (thi - tlo)) * EX_SCALE
        b = (t["final_ovr"] - olo) / (ohi - olo) * EX_SCALE
        lp = legacy_total(name)
        seeds = db.compute_carryover_seeds(t["final_ovr"], t["final_fatigue"], t["final_d_sqrt_raw"],
                                           t["final_pf_norm"], t["final_pa_norm"])
        d, k = seed_in_division[name]
        start[dex] = {
            "name": name, "region": t["region"], "primary_type": t["primary_type"],
            "division": d, "division_seed": k, "basclm": t["basclm"],
            "ex_seed": round(a + b + lp), "ex_parts": {"cups_sqrt": a, "ovr": b, "legacy": lp},
            "starting_fatigue": seeds["fatigue_seed"], "starting_elo": t["final_elo"],
            "seeds": {"ovr": seeds["ovr_seed"], "pf": seeds["pf_seed"], "pa": seeds["pa_seed"],
                      "dscr": seeds["dscr_seed"], "fatigue": seeds["fatigue_seed"]},
        }
        # Recover the stored history from the workbook text plus the season's
        # exported titles, and prove it reproduces the exported roster exactly
        # before adding the World Championship titles.
        exported_titles = []
        stored_items = accolade_items(t["accolades_stored"], [], prev)
        stored_titles = {(ti, s) for ti, ss in stored_items for s in ss}
        for row in (t["accolades_exported"] or "").split("; "):
            parts = [p for p in row.split(" · ") if p]
            for i, part in enumerate(parts):
                ti, ss = db._split_accolade(part)
                if i and parts[0].startswith("World ") and not ti.startswith(("World ", "WCS")):
                    ti = "World " + ti
                exported_titles += [ti for s in ss if s == prev and (ti, s) not in stored_titles]
        if db.merge_accolades(t["accolades_stored"], exported_titles, prev, region_display) \
                != (t["accolades_exported"] or ""):
            raise SystemExit(f"{name}: could not reproduce the exported accolades")
        items = accolade_items(t["accolades_stored"], exported_titles + wc_titles.get(name, []), prev)
        accolades[name] = raw_accolades(items)

    # ---- B9: cup seeding
    elo = {t["name"]: t["final_elo"] for t in teams.values()}
    ovr = {t["name"]: t["final_ovr"] for t in teams.values()}
    draw_order = sorted(elo, key=lambda n: (-elo[n], -ovr[n], n))
    pa_draw = {str(i): n for i, n in enumerate(draw_order, start=1)}
    pa_process = {str((d - 1) * 16 + k): n for n, (d, k) in seed_in_division.items()}
    pa_process = {k: pa_process[k] for k in sorted(pa_process, key=int)}

    regional_pos = {n: i for order in inputs["regional_standings"].values()
                    for i, n in enumerate(order, start=1)}

    def wl(name):
        w, l = shown[name]["regional"].split("-")
        return int(w), int(l)

    rds = {}
    for cup, regions in RDS_CUP_REGIONS.items():
        pool = [n for n in by_name if teams[str(by_name[n])]["region"] in regions]
        pool.sort(key=lambda n: (regional_pos[n], -wl(n)[0], wl(n)[1],
                                 -teams[str(by_name[n])]["dscr_regional_avg_cap400"], -ovr[n], n))
        seeds = {str(i): n for i, n in enumerate(pool, start=1)}
        for i in range(len(pool) + 1, 65):
            seeds[str(i)] = "bye"
        rds[cup] = seeds

    return {
        "league_pods.json": league_pods,
        "start.json": {"season": season, "from_season": prev,
                       "source": f"archive/s{prev}_final.json, archive/s{prev}_inputs.json, legacy_points.json",
                       "ex_seed_scale": {"tot_min": tlo, "tot_max": thi, "ovr_min": olo, "ovr_max": ohi},
                       "teams": start},
        "accolades.json": accolades,
        "rds_seeds.json": rds,
        "pa_draw_seeds.json": pa_draw,
        "pa_process_seeds.json": pa_process,
    }


def main():
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    if len(args) != 1 or not args[0].isdigit():
        sys.exit("usage: python3 prepare_season.py <season> [--force]")
    season = int(args[0])
    os.environ["DECKFIELD_SEASON"] = str(season)
    files = build(season)
    out_dir = os.path.join(HERE, "seasons", f"s{season}")
    os.makedirs(out_dir, exist_ok=True)
    for name, value in files.items():
        path = os.path.join(out_dir, name)
        text = json.dumps(value, ensure_ascii=False, indent=1) + "\n"
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if f.read() == text:
                    print(f"  {os.path.relpath(path, HERE)} already up to date")
                    continue
            if not force:
                sys.exit(f"refusing to overwrite {os.path.relpath(path, HERE)}: it differs from what the "
                         f"frozen record gives now (pass --force only if a rule changed before the season began)")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  wrote {os.path.relpath(path, HERE)}")


if __name__ == "__main__":
    main()
