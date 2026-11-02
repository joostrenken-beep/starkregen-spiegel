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
import geopandas as gpd
import pandas as pd
import rasterio
import pyproj
import yaml
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from starkregen import alkis, dgm, hydro, fetch, hwk, landcover, osm, plausi, preview, schutz, web_export  # noqa: E402
from starkregen.model import analyse_terrain, breach_corridors, manning_depth, run_fill_spill  # noqa: E402


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
    ap.add_argument("--fetch", action="store_true", help="DGM1-Kacheln vom LGLN laden")
    ap.add_argument("--ohne-web", action="store_true", help="keinen Web-Export erzeugen")
    args = ap.parse_args()
    args.block = False
    cfg = yaml.safe_load(Path(args.config).read_text())
    g = cfg["gebiet"]
    bbox = g["test_kachel"] if args.test else g["rechnung"]
    show = g["test_kachel"] if args.test else g["kern"]
    out = ROOT / cfg["ausgabe"]["ordner"] / ("test" if args.test else "pilot")
    run(cfg, bbox, show, out, args)


def run(cfg, bbox, show, out, args):
    """Rechnet ein Gebiet: bbox = Rechengebiet (mit Puffer), show = Kern (Ergebnisse).

    args.block = True: Block einer Stadtberechnung – kein Einzel-Web-Export, keine
    Kontrollbilder/Plausibilitätsbericht; die Ergebnisse des Kerns werden zurückgegeben.
    """
    out.mkdir(parents=True, exist_ok=True)
    g = cfg["gebiet"]
    cell = float(cfg["dgm"]["zellgroesse"])
    block = getattr(args, "block", False)
    result = {"depth": {}, "flows": {}}

    # 1) DGM ------------------------------------------------------------------
    tiles_dir = ROOT / cfg["dgm"]["ordner"]
    if args.fetch:
        log("Lade DGM1 (LGLN STAC) …")
        fetch.dgm_tiles(bbox, tiles_dir, log)
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
    layers = None
    n_culv = 0
    culverts = []
    quelle = cfg["gebaeude"].get("quelle", "alkis")
    alkis_dir = ROOT / "data/raw/alkis"
    if not args.ohne_osm and quelle == "alkis":
        if alkis.find_gpkg(alkis_dir) is None:
            if not args.fetch:
                sys.exit("ALKIS-Geopackage fehlt in data/raw/alkis. Mit --fetch laden.")
            log("Lade ALKIS (LGLN STAC) …")
            fetch.alkis_landkreis(alkis_dir, log=log)
        layers = alkis.load(alkis.find_gpkg(alkis_dir), bbox)
        buildings = layers["buildings"]
        log(f"ALKIS: {len(buildings)} Gebäude, {len(layers['areas'])} Nutzungsflächen, "
            f"{len(layers['addresses'])} Adressen, {len(layers['flurstuecke'])} Flurstücke, "
            f"{len(layers['culverts'])} Durchlässe")
        bid = landcover.rasterize_buildings(buildings, transform, shape)
        sealed, water = alkis.sealed_fraction(layers, transform, shape, cfg)
        n_culv = len(layers["culverts"])
        if cfg["gebaeude"].get("durchlaesse_ableiten", True):
            inf = alkis.infer_culverts(layers)
            if len(inf):
                inf.to_crs("EPSG:4326").to_file(out / "durchlaesse_vermutet.geojson", driver="GeoJSON")
            corr = alkis.corridor_mask(layers, inf, transform, shape)
            dcfg = cfg.get("durchlaesse", {})
            dem_c, n_cells, culverts = breach_corridors(
                dem, valid, corr, max_cut=float(dcfg.get("aushub_schwelle_m", 0.5)))
            diff = dem - dem_c
            write_tif(out / "durchstich_m.tif", np.where(diff > 0, diff, 0), transform, crs)
            # Leistungsfähigkeit je Durchlass: Standard oder Einzelwert nach Lage
            to_wgs = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            feats = []
            for k, c in enumerate(culverts, 1):
                r_, c_ = divmod(c["krone"], shape[1])
                x, y = transform * (c_ + 0.5, r_ + 0.5)
                q = float(dcfg.get("q_standard_m3s", 1.0))
                for ov in dcfg.get("einzelwerte", []) or []:
                    if (ov["x"] - x) ** 2 + (ov["y"] - y) ** 2 <= ov.get("radius_m", 30) ** 2:
                        q = float(ov["q_m3s"])
                c.update({"id": k, "x": x, "y": y, "q_m3s": q})
                lon, lat = to_wgs.transform(x, y)
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                              "properties": {"id": k, "x_utm": round(x, 1), "y_utm": round(y, 1),
                                             "dammhoehe_ueber_sohle_m": round(c["aushub_m"], 2),
                                             "q_m3s": q}})
            (out / "durchlaesse.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
            log(f"Gewässerkorridore: {n_cells} Zellen als offener Graben abgesenkt (≤ "
                f"{dcfg.get('aushub_schwelle_m', 0.5)} m); {len(culverts)} Dammquerungen als Durchlass "
                f"mit begrenzter Leistung (Standard {dcfg.get('q_standard_m3s', 1.0)} m³/s) "
                f"→ durchlaesse.geojson")
            dem = np.where(valid, dem_c, dem)
        log(f"Durchlässe geöffnet: {n_culv}; mittlerer Versiegelungsgrad (ohne Dächer) {sealed.mean():.0%}, "
            f"Dachanteil {(bid > 0).mean():.0%}")
    elif not args.ohne_osm:
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
    # Innenhöfe: Wasser, das nur wegen umschließender Gebäude steht, fließt ab
    # Schwelle (Tor, Durchgang, Türschwelle) über diese Gebäude ab
    hs = cfg["gebaeude"].get("hof_schwelle_m")
    if hs is not None and roof.any():
        dem_low = np.where(valid, np.where(roof, dem + hs, dem), 0.0)
        f_hi = hydro.fill_depressions(dem_b, valid)
        f_lo = hydro.fill_depressions(dem_low, valid)
        pocket = (~roof) & valid & ((f_hi - f_lo) > 0.05)
        near = ndimage.binary_dilation(pocket, structure=np.ones((3, 3)), iterations=2) & roof
        ids = np.unique(bid[near])
        ids = ids[ids > 0]
        lower = np.isin(bid, ids)
        dem_b = np.where(lower, dem_low, dem_b)
        log(f"Innenhöfe/Gebäudenischen: {pocket.sum():,} m² nur durch Gebäude eingeschlossen → "
            f"{len(ids)} Gebäude ab {hs:.2f} m überströmbar")

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
    rcfg = cfg.get("risiko")
    if rcfg and buildings is not None and len(buildings) and layers is not None and "tn__bez" in layers["areas"]:
        kl, gr = schutz.classify(buildings, layers["areas"], cfg)
        buildings["schutz"] = kl
        buildings["schutz_grund"] = gr
        log("Schutzwürdigkeit: " + ", ".join(f"S{k} {schutz.KLASSEN[k]}: {(kl == k).sum()}" for k in (1, 2, 3, 4)))
    nman = np.where(sealed > 0.5, cfg["abfluss"]["manning_n"]["versiegelt"],
                    cfg["abfluss"]["manning_n"]["unversiegelt"])

    rs, cs = crop_index(transform, show, shape)
    left, top = transform * (cs.start, rs.start)
    extent = [left, left + (cs.stop - cs.start) * cell, top - (rs.stop - rs.start) * cell, top]
    stats = {"gebiet": bbox, "anzeige": show, "einzelsenken": int(h.n_labels - 1),
             "durchlaesse": n_culv, "szenarien": {}}

    # 4) Szenarien ------------------------------------------------------------
    plausi_id = cfg["szenarien"][len(cfg["szenarien"]) // 2]["id"]
    vh = cfg.get("vergleich_hwk", {})
    runs = list(cfg["szenarien"]) + ([vh["szenario"]] if vh.get("aktiv") else [])
    web = ROOT / cfg["ausgabe"]["web_ordner"]
    do_web = not args.ohne_web and not args.test and not block
    if do_web:
        web.mkdir(parents=True, exist_ok=True)
    web_files = {"tiefe": {}, "fliesswege": {}}
    web_depths = {}
    tcore = transform * transform.translation(cs.start, rs.start)
    for sc in runs:
        P, T = sc["niederschlag_mm"], sc["dauer_h"]
        r_m, bil = landcover.runoff(P, T, sealed, roof, ring_id, bid, water, cfg,
                                    ohne_verluste=sc.get("ohne_verluste", False))
        r_m = np.where(valid, r_m, 0.0)
        res = run_fill_spill(h, r_m, culverts, T * 3600)
        flow_h = manning_depth(res.flow_volume, res.slope, T * 3600, nman, cell)
        flow_h[res.lake_depth > 0.005] = 0
        depth = np.maximum(res.lake_depth, flow_h)
        depth[roof | ~valid] = 0
        if block:
            write_tif(out / f"{sc['id']}_wassertiefe_kern.tif", depth[rs, cs], tcore, crs)
        else:
            write_tif(out / f"{sc['id']}_wassertiefe.tif", depth, transform, crs)
            write_tif(out / f"{sc['id']}_fliessvolumen.tif", res.flow_volume, transform, crs)
        bil.update({"in_senken_m3": res.stored, "aus_gebiet_m3": res.to_ocean,
                    "durch_durchlaesse_m3": res.durchlass_m3,
                    "bilanzfehler_m3": res.runoff_total - res.stored - res.to_ocean,
                    "flaeche_tiefer_10cm_m2_anzeige": float((depth[rs, cs] > 0.10).sum() * cell * cell),
                    "flaeche_tiefer_30cm_m2_anzeige": float((depth[rs, cs] > 0.30).sum() * cell * cell)})
        # Gebäude-Steckbrief (Vorstufe): Tiefe im Ring um das Gebäude
        if buildings is not None and len(buildings):
            nb = len(buildings)
            m = ring_id > 0
            amp = cfg["ampel"]
            amin = float(amp.get("min_flaeche_m2", 5.0))
            mx = np.zeros(nb + 1)
            np.maximum.at(mx, ring_id[m], depth[m])
            a10 = np.bincount(ring_id[m], weights=(depth[m] >= amp["gelb_ab_m"]) * cell * cell, minlength=nb + 1)
            a30 = np.bincount(ring_id[m], weights=(depth[m] >= amp["rot_ab_m"]) * cell * cell, minlength=nb + 1)
            # Ampel: Tiefe muss auf mindestens amin m² des Rings erreicht werden
            ampel = np.where(a30 >= amin, 2, np.where(a10 >= amin, 1, 0))
            mulde = (mx >= amp["rot_ab_m"]) & (a30 < amin)
            sid = sc["id"]
            buildings[f"{sid}_max_m"] = mx[1:].round(3)
            buildings[f"{sid}_flaeche_ab_gelb_m2"] = a10[1:].round(0)
            buildings[f"{sid}_flaeche_ab_rot_m2"] = a30[1:].round(0)
            buildings[f"{sid}_ampel"] = ampel[1:]
            buildings[f"{sid}_kleine_mulde"] = mulde[1:]
            bil["gebaeude_gelb"] = int((ampel[1:] == 1).sum())
            bil["gebaeude_rot"] = int((ampel[1:] == 2).sum())
            bil["gebaeude_hinweis_kleine_mulde"] = int(mulde[1:].sum())
            if "schutz" in buildings:
                gef = schutz.hazard_level(depth, ring_id, nb, amin, rcfg["gefaehrdung_grenzen_m"])
                ri = schutz.risk(gef, buildings["schutz"].to_numpy(), rcfg["matrix"])
                buildings[f"{sid}_gef"] = gef
                buildings[f"{sid}_risiko"] = ri
                bil["risiko_gebaeude"] = {n: int((ri == k).sum()) for k, n in
                                          enumerate(["gering", "maessig", "hoch", "sehr_hoch"])}
                log(f"  Risiko Gebäude: hoch {bil['risiko_gebaeude']['hoch']}, "
                    f"sehr hoch {bil['risiko_gebaeude']['sehr_hoch']}")
        stats["szenarien"][sc["id"]] = bil
        if do_web and not sc.get("ohne_verluste"):
            sid = sc["id"]
            web_files["tiefe"][sid] = {"url": f"data/tiefe_{sid}.png",
                                       "coords": web_export.depth_png(depth[rs, cs], tcore, crs,
                                                                      web / f"tiefe_{sid}.png")}
            fl = web_export.flow_lines(dem_b, valid, res.source, res.absorb, transform, crs, rs, cs,
                                       min_v=float(cfg.get("web", {}).get("fliessweg_min_m3", 30)))
            (web / f"fliesswege_{sid}.json").write_text(json.dumps(fl, separators=(",", ":")))
            web_files["fliesswege"][sid] = f"data/fliesswege_{sid}.json"
            web_depths[sid] = depth[rs, cs]
            log(f"  Web: Tiefe-Bild + {len(fl['features'])} Fließweg-Linien")
        if block and not sc.get("ohne_verluste"):
            wc = cfg.get("web", {})
            if "near_bld" not in result:
                d = int(wc.get("fliessweg_nahbereich_m", 50) / cell)
                result["near_bld"] = ndimage.maximum_filter(roof.astype(np.uint8), size=2 * d + 1) > 0
            result["depth"][sc["id"]] = depth[rs, cs].astype(np.float32)
            result["flows"][sc["id"]] = web_export.flow_lines(
                dem_b, valid, res.source, res.absorb, transform, crs, rs, cs,
                min_v=float(wc.get("fliessweg_min_m3", 30)), simplify=1.5,
                near=result["near_bld"], min_v_far=float(wc.get("fliessweg_aussen_min_m3", 300)))
        if vh.get("aktiv") and sc["id"] == vh["szenario"]["id"]:
            log("Vergleich mit Hinweiskarte Starkregengefahren (WMS) …")
            ref, unk = hwk.fetch_class_raster(show, vh["wms_layer"])
            own = depth[rs, cs]
            ref = ref[:own.shape[0], :own.shape[1]]
            m = ~roof[rs, cs]
            kz = hwk.compare(own, ref, m)
            kz["unbekannte_farbpixel"] = unk
            # Ampel je Gebäude (gleiche Regel auf beide Karten, nur Kerngebiet)
            if buildings is not None and len(buildings):
                amp = cfg["ampel"]
                amin = float(amp.get("min_flaeche_m2", 5.0))
                rid = ring_id[rs, cs]
                mm = rid > 0
                nb = len(buildings)

                def ampel_of(dd):
                    a10 = np.bincount(rid[mm], weights=(dd[mm] >= amp["gelb_ab_m"]) * 1.0, minlength=nb + 1)
                    a30 = np.bincount(rid[mm], weights=(dd[mm] >= amp["rot_ab_m"]) * 1.0, minlength=nb + 1)
                    return np.where(a30 >= amin, 2, np.where(a10 >= amin, 1, 0))
                inside = np.zeros(nb + 1, bool)
                inside[np.unique(bid[rs, cs][bid[rs, cs] > 0])] = True
                ao, ar = ampel_of(own)[inside], ampel_of(ref)[inside]
                conf = np.zeros((3, 3), int)
                np.add.at(conf, (ar, ao), 1)
                kz["gebaeude_ampel"] = {
                    "anzahl": int(inside.sum()),
                    "gleich": float((ao == ar).mean()),
                    "max_eine_stufe_abweichung": float((np.abs(ao - ar) <= 1).mean()),
                    "matrix_zeilen_hwk_spalten_eigen_gruen_gelb_rot": conf.tolist()}
                log(f"  Gebäude-Ampel: gleich {kz['gebaeude_ampel']['gleich']:.0%}, "
                    f"Matrix (Zeilen HWK grün/gelb/rot, Spalten eigen): {conf.tolist()}")
            stats["vergleich_hwk"] = kz
            write_tif(out / "hwk_referenz_klassen.tif", ref, transform * transform.translation(cs.start, rs.start), crs)
            if block:
                result["hwk_counts"] = hwk.counts(own, ref, m)
            if not block:
                hwk.plot(out / "hwk_vergleich.png", preview.hillshade(dem_b[rs, cs]), own, ref,
                         f"Methodenvergleich: {P} mm in {T} h ohne Kanal/Versickerung – "
                         f"Treffer {kz['10cm']['trefferquote']:.0%}, "
                         f"Fehlalarm {kz['10cm']['fehlalarmquote']:.0%} (≥10 cm)",
                         roof[rs, cs])
            log(f"  ≥10 cm: Treffer {kz['10cm']['trefferquote']:.0%}, Fehlalarm {kz['10cm']['fehlalarmquote']:.0%}, "
                f"CSI {kz['10cm']['csi']:.2f};  ≥30 cm: Treffer {kz['30cm']['trefferquote']:.0%}, "
                f"Fehlalarm {kz['30cm']['fehlalarmquote']:.0%}")
            continue
        if sc["id"] == plausi_id and not block:
            log("Plausibilitätsbericht …")
            plausi.report(out, dem_b[rs, cs], sinks[rs, cs], res.flow_volume[rs, cs], depth[rs, cs],
                          roof[rs, cs], transform * transform.translation(cs.start, rs.start), crs,
                          {k: v.cx[extent[0]:extent[1], extent[2]:extent[3]] for k, v in layers.items()
                           if isinstance(v, gpd.GeoDataFrame)}
                          if layers is not None else None,
                          scenario_label=f"({P} mm in {T} h)")
        if "gebaeude_rot" in bil:
            log(f"  Ampel Gebäude: rot {bil['gebaeude_rot']}, gelb {bil['gebaeude_gelb']}, "
                f"Hinweis kleine Mulde am Haus {bil['gebaeude_hinweis_kleine_mulde']}")
        log(f"{sc['id']}: {P} mm/{T} h → Oberflächenabfluss {bil['oberflaechenabfluss_m3']:,.0f} m³, "
            f"in Senken {res.stored:,.0f} m³, aus Gebiet {res.to_ocean:,.0f} m³, "
            f"Bilanzfehler {bil['bilanzfehler_m3']:.2e} m³")
        if not block:
            preview.save_panel(out / f"{sc['id']}_kontrolle.png", dem_b[rs, cs], extent,
                               roof[rs, cs] if roof.any() else None, depth[rs, cs],
                               res.flow_volume[rs, cs], sinks[rs, cs],
                               f"Starkregen-Spiegel – {P} mm in {T} h (Blockregen) – "
                               "Orientierung, keine amtliche Karte")

    if buildings is not None and len(buildings):
        # Schutzziel (A 118-orientiert): trocken (< 10 cm) bis zum Szenario der Schutzklasse
        if "schutz" in buildings:
            zz = {int(k): v for k, v in rcfg["schutzziel_szenario"].items()}
            ziel = [zz[int(k)] for k in buildings["schutz"]]
            buildings["schutzziel_szenario"] = ziel
            buildings["schutzziel_erreicht"] = [int(buildings.at[i, f"{z}_gef"]) == 0
                                                for i, z in zip(buildings.index, ziel)]
            objs = buildings[buildings["schutz"] >= 3].copy()
            objs["adresse"] = [f"{a} {b}" if isinstance(a, str) else "" for a, b in zip(objs.get("bez"), objs.get("hnr"))]
            objs["max_risiko"] = objs[[f"{x['id']}_risiko" for x in cfg["szenarien"]]].max(axis=1)
            c = objs.geometry.centroid
            objs["x_utm"], objs["y_utm"] = c.x.round(1), c.y.round(1)
            keep = ["adresse", "building", "schutz", "schutz_grund", "schutzziel_szenario", "schutzziel_erreicht",
                    "max_risiko"] + [f"{x['id']}_{k}" for x in cfg["szenarien"] for k in ("gef", "risiko", "max_m")] \
                + ["x_utm", "y_utm"]
            inside = (c.x >= show[0]) & (c.x <= show[2]) & (c.y >= show[1]) & (c.y <= show[3])
            objs[inside].sort_values(["max_risiko", "schutz"], ascending=False)[keep].to_csv(
                out / "risiko_objektliste.csv", index=False, sep=";", decimal=",")
            log(f"Objektliste S3/S4 (Kerngebiet): {int(inside.sum())} Gebäude, davon Schutzziel verfehlt: "
                f"{int((~objs[inside]['schutzziel_erreicht']).sum())} → risiko_objektliste.csv")
        cols = ["osm_id", "geometry"] + [c for c in ("building", "bez", "hnr", "schutz", "schutz_grund",
                                                    "schutzziel_szenario", "schutzziel_erreicht")
                                         if c in buildings.columns]
        cols = cols + [c for c in buildings.columns if c.split("_", 1)[0] in {x["id"] for x in runs}]
        buildings[cols].to_file(out / "gebaeude_ergebnis.gpkg", driver="GPKG")
    (out / "statistik.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))

    # 5) Web-Export ------------------------------------------------------------
    if do_web:
        sids = [x["id"] for x in cfg["szenarien"]]
        sk = web_export.sinks_geojson(sinks[rs, cs], web_depths, tcore, crs)
        (web / "senken.json").write_text(json.dumps(sk, separators=(",", ":")))
        files = dict(web_files, senken="data/senken.json")
        if buildings is not None and len(buildings):
            gb = web_export.buildings_geojson(buildings, sids, show)
            (web / "gebaeude.json").write_text(json.dumps(gb, separators=(",", ":"), ensure_ascii=False))
            files["gebaeude"] = "data/gebaeude.json"
            if layers is not None and "addresses" in layers:
                ad = web_export.addresses_json(layers["addresses"], show, crs)
                (web / "adressen.json").write_text(json.dumps(ad, separators=(",", ":"), ensure_ascii=False))
                files["adressen"] = "data/adressen.json"
        scen = [{"id": x["id"], "mm": x["niederschlag_mm"], "dauer_h": x["dauer_h"],
                 "label": f"{x['niederschlag_mm']} mm in {x['dauer_h']} h"} for x in cfg["szenarien"]]
        web_export.write_meta(web / "meta.json", show, crs, scen, files, {
            "ampel": cfg["ampel"], "kanal_mm_h": cfg["abfluss"]["kanal"]["abschlag_mm_h"],
            "risiko": {"matrix": rcfg["matrix"], "gefaehrdung_grenzen_m": rcfg["gefaehrdung_grenzen_m"],
                       "schutzziel_szenario": {str(k): v for k, v in rcfg["schutzziel_szenario"].items()}}
            if rcfg else None,
            "quellen": ["DGM1 © LGLN (dl-de/by-2-0)", "ALKIS © LGLN (dl-de/by-2-0)"],
            "vergleich_hwk": stats.get("vergleich_hwk", {}).get("gebaeude_ampel")})
        sizes = sum(f.stat().st_size for f in web.glob("*")) / 1e6
        log(f"Web-Export: {web} ({sizes:.1f} MB)")
    log(f"Fertig. Ergebnisse in {out}")
    if block:
        result.pop("near_bld", None)
        result.update({"buildings": buildings, "stats": stats, "crs": crs, "tcore": tcore,
                       "addresses": layers["addresses"] if layers is not None else None,
                       "sinks": web_export.sinks_geojson(sinks[rs, cs], result["depth"], tcore, crs)})
    return result


if __name__ == "__main__":
    main()
