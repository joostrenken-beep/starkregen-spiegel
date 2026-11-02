"""Export der Ergebnisse als statische Dateien für die Web-App (web/public/data).

Erzeugt (Koordinaten WGS84):
  tiefe_<id>.png        eingefärbte max. Wassertiefe (Web-Mercator-Bild + Eckkoordinaten)
  fliesswege_<id>.json  Fließwege als Linien in Fließrichtung (GeoJSON), Eigenschaft v = m³
  senken.json           Senken (Polygone) mit Volumen, Tiefe, Füllgrad je Szenario
  gebaeude.json         Gebäude mit Ampel/Tiefe je Szenario und Adresse
  adressen.json         Adressindex für die Suche [Straße, Nr, lon, lat, Gebäude-ID]
  meta.json             Gebiet, Szenarien, Dateien, Stand
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy import ndimage
from shapely.geometry import LineString, shape
from shapely.ops import unary_union

from . import hydro

# Tiefenklassen (Untergrenze m) -> Farbe RGBA
DEPTH_CLASSES = [
    (0.05, (207, 232, 255, 150)),
    (0.10, (140, 196, 255, 190)),
    (0.30, (74, 144, 226, 215)),
    (0.50, (31, 95, 191, 230)),
    (1.00, (11, 47, 122, 240)),
]


def _r(x, nd=6):
    return round(float(x), nd)


def depth_png(depth, transform, crs, path: Path, res_merc=1.6):
    """Wassertiefe -> Palette-PNG in Web-Mercator. Rückgabe: Eckkoordinaten [[lon,lat] x4]."""
    h, w = depth.shape
    left, top = transform * (0, 0)
    right, bottom = transform * (w, h)
    dst_t, dw, dh = calculate_default_transform(crs, "EPSG:3857", w, h, left, bottom, right, top,
                                                resolution=res_merc)
    dst = np.zeros((dh, dw), np.float32)
    reproject(depth.astype(np.float32), dst, src_transform=transform, src_crs=crs,
              dst_transform=dst_t, dst_crs="EPSG:3857", resampling=Resampling.nearest, src_nodata=None)
    idx = np.zeros(dst.shape, np.uint8)
    for k, (lo, _) in enumerate(DEPTH_CLASSES, 1):
        idx[dst >= lo] = k
    pal = [0, 0, 0] + [c for _, rgba in DEPTH_CLASSES for c in rgba[:3]]
    alpha = bytes([0] + [rgba[3] for _, rgba in DEPTH_CLASSES])
    im = Image.fromarray(idx, mode="P")
    im.putpalette(pal + [0] * (768 - len(pal)))
    im.save(path, optimize=True, transparency=alpha)
    t = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    x0, y0 = dst_t * (0, 0)
    x1, y1 = dst_t * (dw, dh)
    tl, tr, br, bl = (t.transform(x0, y0), t.transform(x1, y0), t.transform(x1, y1), t.transform(x0, y1))
    return [[_r(p[0]), _r(p[1])] for p in (tl, tr, br, bl)]


def flow_lines(dem, valid, source, absorb, transform, crs, rs, cs, min_v=30.0, simplify=1.0):
    """Fließwege (D8) im Anzeigeausschnitt als Linien in Fließrichtung."""
    acc, down = hydro.d8_accumulate(dem, valid, source.reshape(dem.shape), absorb)
    nr, nc = dem.shape
    sel = np.zeros(dem.shape, bool)
    sel[rs, cs] = (acc[rs, cs] >= min_v) & valid[rs, cs] & ~absorb[rs, cs]
    sf = sel.ravel()
    accf = acc.ravel()
    idx = np.flatnonzero(sf)
    dn = down[idx]
    ok = dn >= 0
    inc = np.bincount(dn[ok][sf[dn[ok]]], minlength=nr * nc)
    starts = idx[(inc[idx] == 0) | (inc[idx] >= 2)]
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    feats = []
    for s in starts:
        cells = [s]
        cur = s
        while True:
            nxt = down[cur]
            if nxt < 0:
                break
            cells.append(nxt)
            if not sf[nxt] or inc[nxt] >= 2 or len(cells) > 5000:
                break
            cur = nxt
        if len(cells) < 2:
            continue
        rr, cc = np.divmod(np.array(cells), nc)
        xs, ys = transform * (cc + 0.5, rr + 0.5)
        line = LineString(zip(xs, ys)).simplify(simplify)
        lon, lat = to_wgs.transform(*line.xy)
        v = float(accf[cells[-2]])
        feats.append({"type": "Feature", "properties": {"v": int(round(v))},
                      "geometry": {"type": "LineString",
                                   "coordinates": [[_r(a), _r(b)] for a, b in zip(lon, lat)]}})
    return {"type": "FeatureCollection", "features": feats}


def sinks_geojson(sinks, depths: dict, transform, crs, min_depth=0.10, min_area=25.0):
    lab, n = ndimage.label(sinks > min_depth, structure=np.ones((3, 3)))
    if n == 0:
        return {"type": "FeatureCollection", "features": []}
    idx = np.arange(1, n + 1)
    area = ndimage.sum(np.ones_like(sinks), lab, idx)
    keep = set(idx[area >= min_area].tolist())
    vol = ndimage.sum(np.where(lab > 0, sinks, 0), lab, idx)
    dmax = ndimage.maximum(sinks, lab, idx)
    fill = {k: ndimage.sum(np.where(lab > 0, np.minimum(d, sinks), 0), lab, idx) for k, d in depths.items()}
    polys = {}
    for geom, val in shapes(lab.astype(np.int32), mask=lab > 0, transform=transform):
        v = int(val)
        if v in keep:
            polys.setdefault(v, []).append(shape(geom))
    gdf = gpd.GeoDataFrame(
        [{"geometry": unary_union(g).simplify(0.7), "vol": int(round(vol[v - 1])),
          "tmax": round(float(dmax[v - 1]), 2),
          **{f"f_{k}": round(float(min(1.0, fill[k][v - 1] / max(vol[v - 1], 1e-6))), 2) for k in depths}}
         for v, g in polys.items()], geometry="geometry", crs=crs).to_crs("EPSG:4326")
    return json.loads(gdf.to_json(drop_id=True))


def buildings_geojson(buildings, scen_ids, bounds):
    b = buildings.copy()
    b["id"] = np.arange(1, len(b) + 1)
    c = b.geometry.centroid
    b = b[(c.x >= bounds[0]) & (c.x <= bounds[2]) & (c.y >= bounds[1]) & (c.y <= bounds[3])]
    out = gpd.GeoDataFrame({"id": b["id"]}, geometry=b.geometry.simplify(0.2), crs=b.crs)
    if "bez" in b:
        out["a"] = [f"{s} {n}" if isinstance(s, str) and isinstance(n, str) else ""
                    for s, n in zip(b["bez"], b["hnr"])]
    if "building" in b:
        out["art"] = b["building"].fillna("")
    for s in scen_ids:
        # kompakt: "Ampel|max cm|kleine Mulde"
        out[s] = [f"{int(a)}|{int(round(m * 100))}|{int(bool(k))}" for a, m, k in
                  zip(b[f"{s}_ampel"], b[f"{s}_max_m"], b[f"{s}_kleine_mulde"])]
    gj = json.loads(out.to_crs("EPSG:4326").to_json(drop_id=True))
    for f in gj["features"]:
        _round_geom(f["geometry"])
    return gj


def _round_geom(g):
    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], list) else [_r(c[0]), _r(c[1])]
    g["coordinates"] = rnd(g["coordinates"])


def addresses_json(addresses, bounds, crs):
    a = addresses
    p = a.geometry
    a = a[(p.x >= bounds[0]) & (p.x <= bounds[2]) & (p.y >= bounds[1]) & (p.y <= bounds[3])]
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = t.transform(a.geometry.x.values, a.geometry.y.values)
    rows = [[s, str(n), _r(x, 6), _r(y, 6), int(i) + 1]
            for s, n, x, y, i in zip(a["addr:street"], a["addr:housenumber"], lon, lat, a.index)]
    rows.sort(key=lambda r: (r[0], _hnr_key(r[1])))
    return rows


def _hnr_key(h):
    num = "".join(ch for ch in h if ch.isdigit())
    return (int(num) if num else 0, h)


def write_meta(path: Path, bounds_utm, crs, scenarios, files, extra=None):
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    x0, y0 = t.transform(bounds_utm[0], bounds_utm[1])
    x1, y1 = t.transform(bounds_utm[2], bounds_utm[3])
    meta = {"stand": date.today().isoformat(),
            "gebiet": {"bounds": [_r(x0), _r(y0), _r(x1), _r(y1)],
                       "zentrum": [_r((x0 + x1) / 2), _r((y0 + y1) / 2)]},
            "szenarien": scenarios, "dateien": files,
            "tiefenklassen": [{"ab_m": lo, "farbe": "rgba({},{},{},{:.2f})".format(*c[:3], c[3] / 255)}
                              for lo, c in DEPTH_CLASSES]}
    if extra:
        meta.update(extra)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
