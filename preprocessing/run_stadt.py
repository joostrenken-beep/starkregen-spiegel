"""Starkregen-Spiegel – Berechnung eines ganzen Stadtgebiets in Blöcken.

Das Gebiet (Umriss aus ALKIS) wird in Blöcke von block_km × block_km zerlegt; jeder
Block wird mit puffer_m Rand gerechnet, übernommen wird nur sein Kern. Fertige Blöcke
werden gespeichert – ein Abbruch kann mit demselben Befehl fortgesetzt werden.

Aufruf (aus dem Projektordner):
    python preprocessing/run_stadt.py --config config/osterholz-scharmbeck.yaml --fetch
    python preprocessing/run_stadt.py --config ... --nur-export     # nur Web-Export neu
"""
from __future__ import annotations

import argparse
import json
import pickle
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml
from pyproj import Transformer
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_pipeline  # noqa: E402
from starkregen import fetch, stadt_export, web_export  # noqa: E402
from starkregen.hwk import CLASSES  # noqa: E402,F401


def log(msg, t0=[time.time()]):
    print(f"[{(time.time() - t0[0]) / 60:6.1f} min] {msg}", flush=True)


def blocks_for(outline, size, buffer_m):
    x0, y0, x1, y1 = outline.bounds
    bx0, by0 = int(x0 // size * size), int(y0 // size * size)
    out = []
    for x in range(bx0, int(x1) + 1, size):
        for y in range(by0, int(y1) + 1, size):
            core = box(x, y, x + size, y + size)
            if core.intersects(outline):
                out.append({"id": f"{x // 1000}_{y // 1000}", "kern": [x, y, x + size, y + size],
                            "rechnung": [x - buffer_m, y - buffer_m, x + size + buffer_m, y + size + buffer_m]})
    return out


def compute_blocks(cfg, blocks, work, args):
    sids = [s["id"] for s in cfg["szenarien"]]
    for k, b in enumerate(blocks, 1):
        bdir = work / "bloecke" / b["id"]
        if (bdir / "fertig.pkl").exists():
            log(f"Block {k}/{len(blocks)} {b['id']}: vorhanden")
            continue
        log(f"Block {k}/{len(blocks)} {b['id']}: Kern {b['kern']}")
        if args.fetch:
            fetch.dgm_tiles(b["rechnung"], ROOT / cfg["dgm"]["ordner"], lambda m: None)
        opts = SimpleNamespace(fetch=False, fetch_osm=False, ohne_osm=False, ohne_web=True, test=False, block=True)
        res = run_pipeline.run(cfg, b["rechnung"], b["kern"], bdir, opts)
        kern = b["kern"]
        bld = res["buildings"]
        c = bld.geometry.centroid
        inside = (c.x >= kern[0]) & (c.x < kern[2]) & (c.y >= kern[1]) & (c.y < kern[3])
        bld = bld.assign(_lokal_id=np.arange(1, len(bld) + 1))
        adr = res["addresses"]
        if adr is not None:
            p = adr.geometry
            adr = adr[(p.x >= kern[0]) & (p.x < kern[2]) & (p.y >= kern[1]) & (p.y < kern[3])]
        keep = {
            "buildings": bld[inside.to_numpy()],
            "addresses": adr,
            "klassen": {sid: stadt_export.depth_to_class(res["depth"][sid]) for sid in sids},
            "tcore": res["tcore"], "crs": str(res["crs"]),
            "flows": res["flows"], "sinks": res["sinks"],
            "stats": res["stats"], "hwk": res.get("hwk_counts"),
            "hwk_gebaeude": res["stats"].get("vergleich_hwk", {}).get("gebaeude_ampel"),
        }
        with open(bdir / "fertig.pkl", "wb") as fh:
            pickle.dump(keep, fh, protocol=5)
        # große Zwischenraster des Blocks nicht aufheben
        for f in bdir.glob("*.tif"):
            if not f.name.endswith("_kern.tif"):
                f.unlink()
        del res


def export(cfg, blocks, work, outline, args):
    sids = [s["id"] for s in cfg["szenarien"]]
    web = ROOT / cfg["ausgabe"]["web_ordner"]
    if web.exists():
        shutil.rmtree(web)
    (web / "zellen").mkdir(parents=True)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    city_ll = gpd.GeoSeries([outline], crs="EPSG:25832").to_crs("EPSG:4326").iloc[0]
    bounds_ll = list(city_ll.bounds)

    cells = {}
    adr_rows = []
    klassen = {sid: [] for sid in sids}
    all_bld = []
    hwk_sum = {}
    hwk_bld = np.zeros((3, 3), int)
    stats = {"bloecke": len(blocks), "szenarien": {}}
    crs = None
    for bi, b in enumerate(blocks):
        with open(work / "bloecke" / b["id"] / "fertig.pkl", "rb") as fh:
            r = pickle.load(fh)
        crs = r["crs"]
        for sid in sids:
            klassen[sid].append((r["klassen"][sid], r["tcore"]))
        # Gebäude: globale ID = Blocknummer * 100000 + lokale ID
        bld = r["buildings"].copy()
        bld["_gid"] = (bi + 1) * 100000 + bld["_lokal_id"]
        cen = bld.geometry.centroid
        bld["_zelle"] = [stadt_export.cell_key(x, y) for x, y in zip(cen.x, cen.y)]
        all_bld.append(bld)
        if len(bld):
            gj = web_export.buildings_geojson(bld.drop(columns=["_lokal_id"]), sids, b["kern"])
            # buildings_geojson nummeriert neu – Reihenfolge bleibt erhalten
            for f, gid, z in zip(gj["features"], bld["_gid"], bld["_zelle"]):
                f["properties"]["id"] = int(gid)
                cells.setdefault(z, {"g": [], "f": {s: [] for s in sids}, "s": []})["g"].append(f)
        if r["addresses"] is not None and len(r["addresses"]):
            id_map = dict(zip(bld["_lokal_id"], zip(bld["_gid"], bld["_zelle"])))
            for row in web_export.addresses_json(r["addresses"], b["kern"], crs):
                gid, z = id_map.get(row[4], (0, None))
                if z is None:
                    x, y = to_utm.transform(row[2], row[3])
                    z = stadt_export.cell_key(x, y)
                adr_rows.append(row[:4] + [int(gid), z])
        for sid in sids:
            for f in r["flows"][sid]["features"]:
                x, y = to_utm.transform(*f["geometry"]["coordinates"][0])
                cells.setdefault(stadt_export.cell_key(x, y), {"g": [], "f": {s: [] for s in sids}, "s": []}
                                 )["f"][sid].append(f)
        for f in r["sinks"]["features"]:
            g0 = f["geometry"]["coordinates"][0]
            g0 = g0[0] if isinstance(g0[0][0], list) else g0
            x, y = to_utm.transform(*g0[0])
            cells.setdefault(stadt_export.cell_key(x, y), {"g": [], "f": {s: [] for s in sids}, "s": []})["s"].append(f)
        for sid, v in r["stats"]["szenarien"].items():
            agg = stats["szenarien"].setdefault(sid, {})
            for k2, val in v.items():
                if isinstance(val, (int, float)):
                    agg[k2] = agg.get(k2, 0) + val
        if r["hwk"]:
            for k2, (h, m, fa) in r["hwk"].items():
                s0 = hwk_sum.setdefault(k2, [0, 0, 0])
                s0[0] += h
                s0[1] += m
                s0[2] += fa
        if r["hwk_gebaeude"]:
            hwk_bld += np.array(r["hwk_gebaeude"]["matrix_zeilen_hwk_spalten_eigen_gruen_gelb_rot"])

    # Zellen schreiben
    index = {}
    for key, c in cells.items():
        e, n = map(int, key.split("_"))
        w_, s_ = to_ll.transform(e * 1000, n * 1000)
        e_, n_ = to_ll.transform(e * 1000 + 1000, n * 1000 + 1000)
        index[key] = [round(w_, 5), round(s_, 5), round(e_, 5), round(n_, 5)]
        data = {"g": {"type": "FeatureCollection", "features": c["g"]},
                "f": {s: {"type": "FeatureCollection", "features": c["f"][s]} for s in sids},
                "s": {"type": "FeatureCollection", "features": c["s"]}}
        (web / "zellen" / f"{key}.json").write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    # Ortsteil (Gemarkung) zur Unterscheidung gleicher Straßennamen
    try:
        from starkregen import alkis as _alkis
        gm = gpd.read_file(_alkis.find_gpkg(ROOT / "data/raw/alkis"), layer="flurstuecke",
                           where=f"gem='{cfg['gebiet'].get('gemeindeschluessel', '')}'", columns=["gmk__bez"])
        pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy([r_[2] for r_ in adr_rows], [r_[3] for r_ in adr_rows]),
                               crs="EPSG:4326").to_crs(gm.crs)
        j = gpd.sjoin(pts, gm, how="left", predicate="within")
        j = j[~j.index.duplicated()]
        for r_, gmk in zip(adr_rows, j["gmk__bez"].reindex(range(len(adr_rows)))):
            r_.append(gmk if isinstance(gmk, str) else "außerhalb des Stadtgebiets")
    except Exception as ex:  # Ortsteil ist optional
        log(f"Hinweis: Ortsteile nicht ermittelt ({ex})")
    adr_rows.sort(key=lambda r_: (r_[0], web_export._hnr_key(r_[1])))
    (web / "adressen.json").write_text(json.dumps(adr_rows, separators=(",", ":"), ensure_ascii=False))
    gpd.GeoDataFrame(geometry=[outline.simplify(15)], crs="EPSG:25832").to_crs("EPSG:4326").to_file(
        web / "stadtgebiet.json", driver="GeoJSON")
    log(f"Web: {len(index)} Zellen, {len(adr_rows)} Adressen")

    tiles = {}
    for sid in sids:
        n = stadt_export.class_pmtiles(klassen[sid], crs, web / f"tiefe_{sid}.pmtiles", bounds_ll,
                                       minzoom=cfg["web"].get("minzoom", 11), maxzoom=cfg["web"].get("maxzoom", 16))
        tiles[sid] = f"data/tiefe_{sid}.pmtiles"
        log(f"  {sid}: {n} Rasterkacheln")

    # Gebäude gesamt (Analyse) + Objektliste S3/S4 im Stadtgebiet
    bld = pd.concat(all_bld, ignore_index=True)
    bld = gpd.GeoDataFrame(bld, geometry="geometry", crs=crs)
    in_city = bld.geometry.centroid.within(outline)
    out = ROOT / cfg["ausgabe"]["ordner"] / "stadt"
    cols = [c for c in bld.columns if not c.startswith("_") and c not in ("osm_id",)]
    bld[cols].to_file(out / "gebaeude_ergebnis.gpkg", driver="GPKG")
    if "schutz" in bld:
        objs = bld[in_city & (bld["schutz"] >= 3)].copy()
        objs["adresse"] = [f"{a} {b_}" if isinstance(a, str) else "" for a, b_ in zip(objs.get("bez"), objs.get("hnr"))]
        objs["max_risiko"] = objs[[f"{s}_risiko" for s in sids]].max(axis=1)
        c = objs.geometry.centroid
        objs["x_utm"], objs["y_utm"] = c.x.round(1), c.y.round(1)
        keep = ["adresse", "building", "schutz", "schutz_grund", "schutzziel_szenario", "schutzziel_erreicht",
                "max_risiko"] + [f"{s}_{k}" for s in sids for k in ("gef", "risiko", "max_m")] + ["x_utm", "y_utm"]
        objs.sort_values(["max_risiko", "schutz"], ascending=False)[keep].to_csv(
            out / "risiko_objektliste.csv", index=False, sep=";", decimal=",")
        stats["gebaeude_stadtgebiet"] = int(in_city.sum())
        stats["schutzklassen"] = {f"S{k}": int(((bld["schutz"] == k) & in_city).sum()) for k in (1, 2, 3, 4)}
        stats["s4_schutzziel_verfehlt"] = int((~bld[in_city & (bld["schutz"] == 4)]["schutzziel_erreicht"]).sum())
        stats["risiko"] = {sid: {n: int(((bld[f"{sid}_risiko"] == k) & in_city).sum())
                                 for k, n in enumerate(["gering", "maessig", "hoch", "sehr_hoch"])} for sid in sids}
    if hwk_sum:
        stats["vergleich_hwk"] = {k: {"trefferquote": h / max(1, h + m), "fehlalarmquote": fa / max(1, h + fa),
                                      "csi": h / max(1, h + m + fa)} for k, (h, m, fa) in hwk_sum.items()}
        stats["vergleich_hwk"]["gebaeude_ampel_gleich"] = float(np.trace(hwk_bld) / max(1, hwk_bld.sum()))
        stats["vergleich_hwk"]["gebaeude_matrix"] = hwk_bld.tolist()
    (out / "statistik.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))

    from starkregen import kostra
    cx, cy = outline.centroid.x, outline.centroid.y
    hn60 = kostra.werte(ROOT / "data/raw/kostra", 60, cx, cy)
    kk = cfg["abfluss"]["kanal"].get("kostra")
    kanal = (kostra.werte(ROOT / "data/raw/kostra", int(kk["dauer_min"]), cx, cy)[int(kk["wiederkehr_a"])]
             * 60 / int(kk["dauer_min"])) if kk else cfg["abfluss"]["kanal"]["abschlag_mm_h"]
    scen = [{"id": x["id"], "mm": x["niederschlag_mm"], "dauer_h": x["dauer_h"],
             "label": f"{x['niederschlag_mm']} mm in {x['dauer_h']} h",
             "jaehrlichkeit": kostra.jaehrlichkeit_text(x["niederschlag_mm"], hn60) if x["dauer_h"] == 1 else ""}
            for x in cfg["szenarien"]]
    rcfg = cfg.get("risiko")
    web_export.write_meta(web / "meta.json", outline.bounds, "EPSG:25832", scen,
                          {"tiefe_pmtiles": tiles, "zellen": index, "zellen_url": "data/zellen/",
                           "adressen": "data/adressen.json", "umriss": "data/stadtgebiet.json"}, {
        "modus": "zellen", "name": cfg["gebiet"]["name"],
        "zellen_minzoom": cfg["web"].get("zellen_minzoom", 14),
        "ampel": cfg["ampel"], "kanal_mm_h": round(kanal, 1),
        "kostra_d60": hn60,
        "risiko": {"matrix": rcfg["matrix"], "gefaehrdung_grenzen_m": rcfg["gefaehrdung_grenzen_m"],
                   "schutzziel_szenario": {str(k): v for k, v in rcfg["schutzziel_szenario"].items()}} if rcfg else None,
        "quellen": ["DGM1 © LGLN (dl-de/by-2-0)", "ALKIS © LGLN (dl-de/by-2-0)"],
        "vergleich_hwk": stats.get("vergleich_hwk")})
    size = sum(f.stat().st_size for f in web.rglob("*") if f.is_file()) / 1e6
    log(f"Web-Export fertig: {web} ({size:.1f} MB)")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/osterholz-scharmbeck.yaml"))
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--nur-export", action="store_true")
    ap.add_argument("--max-bloecke", type=int, default=0, help="nur die ersten N Blöcke (Test)")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    g = cfg["gebiet"]
    umriss = ROOT / g["umriss"]
    if not umriss.exists():
        from starkregen import alkis
        gpkg = alkis.find_gpkg(ROOT / "data/raw/alkis")
        if gpkg is None:
            fetch.alkis_landkreis(ROOT / "data/raw/alkis", log=log)
            gpkg = alkis.find_gpkg(ROOT / "data/raw/alkis")
        fs = gpd.read_file(gpkg, layer="flurstuecke", where=f"gem='{g['gemeindeschluessel']}'", columns=["gem"])
        gpd.GeoDataFrame(geometry=[fs.geometry.union_all().buffer(0)], crs=fs.crs).to_file(umriss)
        log(f"Stadtumriss aus ALKIS erzeugt: {umriss}")
    outline = gpd.read_file(umriss).to_crs("EPSG:25832").geometry.union_all()
    blocks = blocks_for(outline, int(g["block_km"] * 1000), int(g["puffer_m"]))
    # Offene Blöcke in kleinere Teilblöcke zerlegen (kürzere Rechenzeit je gespeichertem Stück)
    teil = int(g.get("teilblock_km", 0) * 1000)
    work0 = ROOT / cfg["ausgabe"]["ordner"] / "stadt" / "bloecke"
    if teil and teil < g["block_km"] * 1000:
        neu = []
        for b in blocks:
            if (work0 / b["id"] / "fertig.pkl").exists():
                neu.append(b)
                continue
            x0, y0, x1, y1 = b["kern"]
            for x in range(x0, x1, teil):
                for y in range(y0, y1, teil):
                    if box(x, y, x + teil, y + teil).intersects(outline):
                        bf = int(g["puffer_m"])
                        neu.append({"id": f"{x // 1000}_{y // 1000}_{teil // 1000}km",
                                    "kern": [x, y, x + teil, y + teil],
                                    "rechnung": [x - bf, y - bf, x + teil + bf, y + teil + bf]})
        blocks = neu
    if args.max_bloecke:
        blocks = blocks[:args.max_bloecke]
    work = ROOT / cfg["ausgabe"]["ordner"] / "stadt"
    work.mkdir(parents=True, exist_ok=True)
    log(f"{g['name']}: {outline.area / 1e6:.1f} km², {len(blocks)} Blöcke à {g['block_km']} km "
        f"(+{g['puffer_m']} m Puffer)")
    if not args.nur_export:
        compute_blocks(cfg, blocks, work, args)
    stats = export(cfg, blocks, work, outline, args)
    log(json.dumps({k: v for k, v in stats.items() if k in ("gebaeude_stadtgebiet", "schutzklassen",
                                                           "s4_schutzziel_verfehlt")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
