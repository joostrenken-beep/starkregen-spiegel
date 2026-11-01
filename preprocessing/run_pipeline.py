"""Starkregen-Spiegel – Vorverarbeitung (Meilenstein 1).

Aufruf (aus dem Projektordner):
    python preprocessing/run_pipeline.py --test        # nur Testkachel
    python preprocessing/run_pipeline.py               # ganzes Rechengebiet
Optionen:
    --fetch-osm   OSM-Daten über Overpass laden, falls noch nicht vorhanden
    --ohne-osm    ohne Gebäude/Landnutzung rechnen (reiner DGM-Test)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from starkregen import dgm, landcover, osm, preview  # noqa: E402
from starkregen.model import analyse_terrain, manning_depth, run_fill_spill  # noqa: E402


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:7.1f} s] {msg}", flush=True)


def write_tif(path, arr, transform, crs, dtype="float32", nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
                       compress="deflate", tiled=True, predictor=2 if dtype.startswith("float") else 1) as d:
        d.write(arr.astype(dtype), 1)


def crop_index(transform, bbox, shape):
    inv = ~transform
    c0, r0 = inv * (bbox[0], bbox[3])
    c1, r1 = inv * (bbox[2], bbox[1])
    r0, c0 = max(0, int(round(r0))), max(0, int(round(c0)))
    r1, c1 = min(shape[0], int(round(r1))), min(shape[1], int(round(c1)))
    return slice(r0, r1), slice(c0, c1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/pilot.yaml"))
    ap.add_argument("--test", action="store_true", help="nur Testkachel rechnen")
    ap.add_argument("--fetch-osm", action="store_true")
    ap.add_argument("--ohne-osm", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    g = cfg["gebiet"]
    bbox = g["test_kachel"] if args.test else g["rechnung"]
    show = g["test_kachel"] if args.test else g["kern"]
    out = ROOT / cfg["ausgabe"]["ordner"] / ("test" if args.test else "pilot")
    out.mkdir(parents=True, exist_ok=True)
    cell = float(cfg["dgm"]["zellgroesse"])

    # 1) DGM ------------------------------------------------------------------
    tiles_dir = ROOT / cfg["dgm"]["ordner"]
    miss = dgm.missing_tiles(tiles_dir, bbox) if dgm.find_tile_files(tiles_dir) else dgm.needed_tiles(bbox)
    if miss:
        log(f"Hinweis: fehlende Kacheln (E_km, N_km): {miss}")
    dem0, transform, crs = dgm.mosaic(tiles_dir, bbox)
    valid = ~np.isnan(dem0)
    log(f"DGM: {dem0.shape[1]}×{dem0.shape[0]} Zellen, Höhe {np.nanmin(dem0):.2f} … {np.nanmax(dem0):.2f} m, "
        f"NoData {(~valid).mean() * 100:.2f} %")

    # 2) OSM ------------------------------------------------------------------
    shape = dem0.shape
    bid = np.zeros(shape, np.int32)
    sealed = np.zeros(shape)
    water = np.zeros(shape, bool)
    dem = dem0.copy()
    buildings = None
    n_culv = 0
    if not args.ohne_osm:
        osm_path = ROOT / "data/raw/osm/osm_rechengebiet.json"
        if not osm_path.exists():
            if not args.fetch_osm:
                sys.exit(f"OSM-Datei fehlt: {osm_path}. Mit --fetch-osm laden oder --ohne-osm rechnen.")
            log("Lade OSM über Overpass …")
            osm.fetch(g["rechnung"], osm_path)
        layers = osm.load(osm_path, crs=str(crs))
        buildings = layers["buildings"].reset_index(drop=True)
        log(f"OSM: {len(buildings)} Gebäude, {len(layers['highways'])} Wege, "
            f"{len(layers['areas'])} Flächen, {len(layers['addresses'])} Adressen")
        bid = landcover.rasterize_buildings(buildings, transform, shape)
        sealed, water = landcover.sealed_fraction(layers, transform, shape, cfg)
        dem, n_culv = landcover.burn_culverts(dem, layers["waterways"], transform)
        log(f"Durchlässe geöffnet: {n_culv}")
    roof = bid > 0
    dem_b = np.where(roof, dem + cfg["gebaeude"]["einbrennhoehe"], dem)
    dem_b = np.where(valid, dem_b, 0.0)

    # 3) Geländeanalyse (szenariounabhängig) ------------------------------------
    log("Senkenhierarchie …")
    h = analyse_terrain(dem_b, valid, roof, cell_area=cell * cell)
    sinks = h.sink_depth_full()
    sinks[roof] = 0
    n_roots = int((h.parent < 0).sum()) - 1
    log(f"{h.n_labels - 1} Einzelsenken, {h.n_nodes - h.n_labels} Zusammenschlüsse, "
        f"Gesamtvolumen aller Senken {sinks.sum() * cell * cell:,.0f} m³")
    write_tif(out / "senken_tiefe.tif", np.where(valid, sinks, np.nan), transform, crs)
    write_tif(out / "dgm_eingebrannt.tif", np.where(valid, dem_b, np.nan), transform, crs)

    ring_id = landcover.building_ring(bid, cfg["gebaeude"]["ring_breite"])
    nman = np.where(sealed > 0.5, cfg["abfluss"]["manning_n"]["versiegelt"],
                    cfg["abfluss"]["manning_n"]["unversiegelt"])

    rs, cs = crop_index(transform, show, shape)
    left, top = transform * (cs.start, rs.start)
    extent = [left, left + (cs.stop - cs.start) * cell, top - (rs.stop - rs.start) * cell, top]
    stats = {"gebiet": bbox, "anzeige": show, "einzelsenken": int(h.n_labels - 1),
             "durchlaesse": n_culv, "szenarien": {}}

    # 4) Szenarien ------------------------------------------------------------
    for sc in cfg["szenarien"]:
        P, T = sc["niederschlag_mm"], sc["dauer_h"]
        r_m, bil = landcover.runoff(P, T, sealed, roof, ring_id, bid, water, cfg)
        r_m = np.where(valid, r_m, 0.0)
        res = run_fill_spill(h, r_m)
        flow_h = manning_depth(res.flow_volume, res.slope, T * 3600, nman, cell)
        flow_h[res.lake_depth > 0.005] = 0
        depth = np.maximum(res.lake_depth, flow_h)
        depth[roof | ~valid] = 0
        write_tif(out / f"{sc['id']}_wassertiefe.tif", depth, transform, crs)
        write_tif(out / f"{sc['id']}_fliessvolumen.tif", res.flow_volume, transform, crs)
        bil.update({"in_senken_m3": res.stored, "aus_gebiet_m3": res.to_ocean,
                    "bilanzfehler_m3": res.runoff_total - res.stored - res.to_ocean,
                    "flaeche_tiefer_10cm_m2_anzeige": float((depth[rs, cs] > 0.10).sum() * cell * cell),
                    "flaeche_tiefer_30cm_m2_anzeige": float((depth[rs, cs] > 0.30).sum() * cell * cell)})
        # Gebäude-Steckbrief (Vorstufe): max. Tiefe im Ring
        if buildings is not None and len(buildings):
            nb = len(buildings)
            m = ring_id > 0
            mx = np.zeros(nb + 1)
            np.maximum.at(mx, ring_id[m], depth[m])
            amp = cfg["ampel"]
            col = f"{sc['id']}_max_m"
            buildings[col] = mx[1:].round(3)
            bil["gebaeude_gelb"] = int(((mx[1:] >= amp["gelb_ab_m"]) & (mx[1:] < amp["rot_ab_m"])).sum())
            bil["gebaeude_rot"] = int((mx[1:] >= amp["rot_ab_m"]).sum())
        stats["szenarien"][sc["id"]] = bil
        log(f"{sc['id']}: {P} mm/{T} h → Oberflächenabfluss {bil['oberflaechenabfluss_m3']:,.0f} m³, "
            f"in Senken {res.stored:,.0f} m³, aus Gebiet {res.to_ocean:,.0f} m³, "
            f"Bilanzfehler {bil['bilanzfehler_m3']:.2e} m³")
        preview.save_panel(out / f"{sc['id']}_kontrolle.png", dem_b[rs, cs], extent,
                           roof[rs, cs] if roof.any() else None, depth[rs, cs],
                           res.flow_volume[rs, cs], sinks[rs, cs],
                           f"Starkregen-Spiegel – {P} mm in {T} h (Blockregen) – Orientierung, keine amtliche Karte")

    if buildings is not None and len(buildings):
        cols = ["osm_id", "geometry"] + [c for c in buildings.columns if c.endswith("_max_m")]
        buildings[cols].to_file(out / "gebaeude_ergebnis.gpkg", driver="GPKG")
    (out / "statistik.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    log(f"Fertig. Ergebnisse in {out}")


if __name__ == "__main__":
    main()
