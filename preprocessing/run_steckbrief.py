"""Starkregen-Spiegel – Meilenstein 3: Grundstücks-Steckbrief, Einzugsgebiete, 3D-Daten.

Setzt auf die fertige Stadtberechnung (run_stadt.py) auf und ergänzt die Web-Daten um
  steckbrief/<Zelle>.json   je Gebäude: Flurstück, Wasser auf dem Grundstück, Einzugsgebiet
  gelaende.pmtiles          Geländekacheln (DGM1) für die 3D-Ansicht
  Gebäudehöhe "h" und Zelle "c" in zellen/<Zelle>.json

Aufruf (aus dem Projektordner):
    python preprocessing/run_steckbrief.py --config config/osterholz-scharmbeck.yaml
    python preprocessing/run_steckbrief.py --nur-export   # nach einem neuen run_stadt-Export
Fertige Blöcke (bloecke/<id>/m3.pkl) werden übersprungen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import yaml
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_stadt import stadt_bloecke  # noqa: E402
from starkregen import alkis, dgm, fetch, stadt_export, steckbrief  # noqa: E402

DOM_DIR = ROOT / "data/raw/dom1"


def log(msg, t0=[time.time()]):
    print(f"[{(time.time() - t0[0]) / 60:6.1f} min] {msg}", flush=True)


def default_height(s):
    return 3.0 if s == 1 else 7.0


def compute_block(cfg, b, bdir, sids):
    gpkg = alkis.find_gpkg(ROOT / "data/raw/alkis")
    with open(bdir / "fertig.pkl", "rb") as fh:
        r = pickle.load(fh)
    core = r["buildings"].reset_index(drop=True)
    calc = b["rechnung"]
    geb = steckbrief.read_layer(gpkg, "gebaeude", calc, columns=["objid", "ofl__bez"])
    geb = geb[~geb["ofl__bez"].fillna("").isin(steckbrief.ALKIS_SKIP)].reset_index(drop=True)
    fs = steckbrief.read_layer(gpkg, "flurstuecke", calc, columns=["objid", "gmk__bez", "fln", "fs_text", "afl"])
    out = {"lokal_id": core["_lokal_id"].to_numpy(), "hoehe": None, "flurstueck": None,
           "flurstueck_info": {}, "flurstueck_werte": {}, "einzug": {}}
    if len(core) == 0:
        return out

    # 1) Gebäudehöhen aus DOM1 − DGM1
    x0, y0, x1, y1 = core.total_bounds
    hb = [np.floor(x0) - 2, np.floor(y0) - 2, np.ceil(x1) + 2, np.ceil(y1) + 2]
    fetch.dom_tiles(hb, DOM_DIR, lambda m: None)
    dom, tr, _ = dgm.mosaic(DOM_DIR, hb)
    dgm_h, tr2, _ = dgm.mosaic(ROOT / cfg["dgm"]["ordner"], hb)
    h = steckbrief.building_heights(core.geometry, dom, dgm_h, tr)
    s = core["schutz"].to_numpy() if "schutz" in core else np.full(len(core), 2)
    bad = ~np.isfinite(h) | (h < 2.0) | (h > 80)
    h = np.where(bad, [default_height(x) for x in s], h)
    # Bäume über kleinen Gebäuden verfälschen das DOM: Höhen begrenzen
    h = np.minimum(h, np.where(s == 1, 6.0, 30.0))
    out["hoehe"] = np.round(h, 1)
    log(f"    Höhen: Median {np.median(h):.1f} m, {int(bad.sum())} mit Standardhöhe")
    del dom, dgm_h

    # 2) Flurstück und Wasser auf dem Grundstück (aus den Tiefenrastern des Blockkerns)
    own = steckbrief.parcel_of(core, fs)
    out["flurstueck"] = own
    used = set(o for o in own if isinstance(o, str))
    for row in fs[fs["objid"].isin(used)].itertuples():
        out["flurstueck_info"][row.objid] = (row.gmk__bez, int(row.fln) if row.fln == row.fln else None,
                                             row.fs_text, int(row.afl) if row.afl == row.afl else None)
    depth, ktr = {}, None
    for sid in sids:
        with rasterio.open(bdir / f"{sid}_wassertiefe_kern.tif") as src:
            depth[sid] = src.read(1)
            ktr = src.transform
    fs_k = fs.cx[b["kern"][0]:b["kern"][2], b["kern"][1]:b["kern"][3]].reset_index(drop=True)
    out["flurstueck_werte"] = steckbrief.parcel_stats(fs_k, geb, depth, ktr)
    del depth

    # 3) Einzugsgebiete auf dem Rechengebiet des Blocks (Kern + Puffer)
    dgm_c, tr_c, _ = dgm.mosaic(ROOT / cfg["dgm"]["ordner"], calc)
    plab = steckbrief._raster(list(fs.geometry), tr_c, dgm_c.shape)
    fs_index = {oid: k + 1 for k, oid in enumerate(fs["objid"])}
    own_lab = np.array([fs_index.get(o, 0) for o in own], dtype=np.int64)
    gi = {oid: k for k, oid in enumerate(geb["objid"])}
    core_idx = np.array([gi.get(o, -1) for o in core["osm_id"]], dtype=np.int64)
    ok = core_idx >= 0
    res = steckbrief.catchments(dgm_c, tr_c, geb, core_idx[ok], plab, own_lab[ok],
                                burn=float(cfg["gebaeude"].get("einbrennhoehe", 5)),
                                ring=int(cfg["gebaeude"].get("ring_breite", 2)), log=log)
    pos = np.flatnonzero(ok)
    for t, i in enumerate(core_idx[ok]):
        if i in res:
            out["einzug"][int(pos[t])] = res[i]
    n_poly = sum(1 for v in out["einzug"].values() if v[3] is not None)
    log(f"    Einzugsgebiete: {len(out['einzug'])}, davon {n_poly} mit Zufluss von außen")
    return out


def compute(cfg, blocks, work):
    sids = [s["id"] for s in cfg["szenarien"]]
    for k, b in enumerate(blocks, 1):
        bdir = work / "bloecke" / b["id"]
        if (bdir / "m3.pkl").exists():
            log(f"Block {k}/{len(blocks)} {b['id']}: vorhanden")
            continue
        log(f"Block {k}/{len(blocks)} {b['id']}")
        res = compute_block(cfg, b, bdir, sids)
        with open(bdir / "m3.pkl.part", "wb") as fh:
            pickle.dump(res, fh, protocol=5)
        (bdir / "m3.pkl.part").rename(bdir / "m3.pkl")


def _coords(poly, t):
    ring = lambda c: [[round(x, 6), round(y, 6)] for x, y in zip(*t.transform(*zip(*c)))]  # noqa: E731
    return [ring(poly.exterior.coords)] + [ring(h.coords) for h in poly.interiors]


def export(cfg, blocks, work, outline):
    sids = [s["id"] for s in cfg["szenarien"]]
    web = ROOT / cfg["ausgabe"]["web_ordner"]
    to_ll = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    per_gid, fs_info, fs_vals = {}, {}, {}
    for bi, b in enumerate(blocks):
        with open(work / "bloecke" / b["id"] / "m3.pkl", "rb") as fh:
            m = pickle.load(fh)
        fs_info.update(m["flurstueck_info"])
        steckbrief.merge_parcel_stats(fs_vals, m["flurstueck_werte"])
        if m["hoehe"] is None:
            continue
        for t, lid in enumerate(m["lokal_id"]):
            gid = (bi + 1) * 100000 + int(lid)
            e = m["einzug"].get(t)
            per_gid[gid] = {"h": float(m["hoehe"][t]), "fs": m["flurstueck"][t], "e": e}

    meta = json.loads((web / "meta.json").read_text())
    (web / "steckbrief").mkdir(exist_ok=True)
    n_cells = n_poly = 0
    for f in sorted((web / "zellen").glob("*.json")):
        key = f.stem
        data = json.loads(f.read_text())
        sb = {}
        for ft in data["g"]["features"]:
            p = ft["properties"]
            info = per_gid.get(p["id"])
            p["c"] = key
            if not info:
                continue
            p["h"] = info["h"]
            rec = {}
            oid = info["fs"]
            if isinstance(oid, str) and oid in fs_info:
                gmk, fln, txt, afl = fs_info[oid]
                rec["fs"] = {"t": f"Gemarkung {gmk}, Flur {fln}, Flurstück {txt}" if fln else f"Gemarkung {gmk}, Flurstück {txt}",
                             "fl": afl}
                v = fs_vals.get(oid)
                if v:
                    rec["fs"].update({sid: [int(round(v[sid][0] * 100)), int(round(v[sid][1])), int(round(v[sid][2]))]
                                      for sid in sids})
            e = info["e"]
            if e:
                area, ext, trunc, poly = e
                rec["e"] = {"a": int(round(area)), "x": int(round(ext * 100)), "t": int(trunc)}
                if poly is not None and not poly.is_empty:
                    rec["e"]["g"] = _coords(poly, to_ll)
                    n_poly += 1
            sb[str(p["id"])] = rec
        f.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        (web / "steckbrief" / f"{key}.json").write_text(json.dumps(sb, separators=(",", ":"), ensure_ascii=False))
        n_cells += 1
    log(f"Steckbriefe: {n_cells} Zellen, {len(per_gid)} Gebäude, {n_poly} Einzugsgebiete als Fläche")

    # Geländekacheln für 3D
    b_ll = meta["gebiet"]["bounds"]
    t = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    x0, y0 = t.transform(b_ll[0], b_ll[1])
    x1, y1 = t.transform(b_ll[2], b_ll[3])
    files = []
    for f in dgm.find_tile_files(ROOT / cfg["dgm"]["ordner"]):
        with rasterio.open(f) as s:
            bb = s.bounds
            if bb.right > x0 - 1500 and bb.left < x1 + 1500 and bb.top > y0 - 1500 and bb.bottom < y1 + 1500:
                files.append(f)
    n = stadt_export.terrain_pmtiles(files, "EPSG:25832", web / "gelaende.pmtiles", b_ll,
                                     minzoom=10, maxzoom=int(cfg["web"].get("gelaende_maxzoom", 15)), log=log)
    log(f"Gelände: {n} Kacheln ({(web / 'gelaende.pmtiles').stat().st_size / 1e6:.1f} MB)")

    meta["dateien"]["steckbrief_url"] = "data/steckbrief/"
    meta["dateien"]["gelaende_pmtiles"] = "data/gelaende.pmtiles"
    n_mk = write_markierung(web, cfg, meta)
    log(f"Markiert: {n_mk} Gebäude")
    meta["version"] = hashlib.sha1(f"{meta['version']}-m3-{time.time()}".encode()).hexdigest()[:10]
    (web / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    log("Steckbrief-Export fertig")


def write_markierung(web, cfg, meta):
    """Übersicht der markierten Gebäude (z. B. S4 mit ≥ 30 cm bei T ≈ 30 a) als Punkte –
    damit sie auch in kleinen Zoomstufen sichtbar sind (Gebäude selbst erst ab Zoom 14)."""
    mk = cfg["web"].get("markierung", {"szenario": "p30", "gefaehrdung_ab": 2, "schutzklasse": 4})
    feats = []
    for f in sorted((web / "zellen").glob("*.json")):
        for ft in json.loads(f.read_text())["g"]["features"]:
            p = ft["properties"]
            if p.get("s") == mk["schutzklasse"] and p.get(f"g_{mk['szenario']}", 0) >= mk["gefaehrdung_ab"]:
                ring = ft["geometry"]["coordinates"][0]
                ring = ring[0] if isinstance(ring[0][0], list) else ring
                x = sum(c[0] for c in ring) / len(ring)
                y = sum(c[1] for c in ring) / len(ring)
                cm = int(p[mk["szenario"]].split("|")[1])
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(x, 6), round(y, 6)]},
                              "properties": {"id": p["id"], "a": p.get("a", ""), "art": p.get("art", ""), "cm": cm, "c": p.get("c")}})
    (web / "markiert.json").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                  separators=(",", ":"), ensure_ascii=False))
    meta["markierung"] = {**mk, "datei": "data/markiert.json", "anzahl": len(feats)}
    return len(feats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config/osterholz-scharmbeck.yaml"))
    ap.add_argument("--nur-export", action="store_true")
    ap.add_argument("--max-bloecke", type=int, default=0)
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    outline, blocks = stadt_bloecke(cfg)
    work = ROOT / cfg["ausgabe"]["ordner"] / "stadt"
    if not args.nur_export:
        compute(cfg, blocks[:args.max_bloecke] if args.max_bloecke else blocks, work)
    if not args.max_bloecke:
        export(cfg, blocks, work, outline)


if __name__ == "__main__":
    main()
