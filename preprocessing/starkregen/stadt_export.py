"""Zusammenführen der Blockergebnisse einer Stadtberechnung für die Web-App.

  tiefe_<id>.pmtiles   Wassertiefe als Rasterkacheln (PNG, Web-Mercator, Pyramide z_min..z_max)
  zellen/<E>_<N>.json  1-km-Pakete: Gebäude, Fließwege je Szenario, Senken
  adressen.json        Adressindex der ganzen Stadt [Straße, Nr, lon, lat, Gebäude-ID, Zelle]
  stadtgebiet.json     Umriss (vereinfacht)
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from .web_export import DEPTH_CLASSES

R = 6378137.0
ORIGIN = math.pi * R


def depth_to_class(depth):
    out = np.zeros(depth.shape, np.uint8)
    for k, (lo, _) in enumerate(DEPTH_CLASSES, 1):
        out[depth >= lo] = k
    return out


def _palette_png(idx):
    pal = [0, 0, 0] + [c for _, rgba in DEPTH_CLASSES for c in rgba[:3]]
    alpha = bytes([0] + [rgba[3] for _, rgba in DEPTH_CLASSES])
    im = Image.fromarray(idx, mode="P")
    im.putpalette(pal + [0] * (768 - len(pal)))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True, transparency=alpha)
    return buf.getvalue()


def class_pmtiles(blocks, crs, out_path: Path, bounds_ll, minzoom=11, maxzoom=16,
                  uebersicht_bis_zoom=13, uebersicht_ab_klasse=3):
    """blocks: Liste (klassen-array uint8, transform) im Ziel-KBS (UTM).
    Erzeugt eine PMTiles-Datei mit Palette-PNG-Kacheln; tiefere Zoomstufen per Maximum
    (dünne Wasserläufe bleiben sichtbar)."""
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = t.transform(bounds_ll[0], bounds_ll[1])
    x1, y1 = t.transform(bounds_ll[2], bounds_ll[3])
    res = 2 * ORIGIN / (256 * 2 ** maxzoom)
    step = 2 ** (maxzoom - minzoom)
    tx0 = int((x0 + ORIGIN) // (256 * res)) // step * step
    ty0 = int((ORIGIN - y1) // (256 * res)) // step * step
    tx1 = (int((x1 + ORIGIN) // (256 * res)) // step + 1) * step
    ty1 = (int((ORIGIN - y0) // (256 * res)) // step + 1) * step
    W, H = (tx1 - tx0) * 256, (ty1 - ty0) * 256
    dst_t = from_origin(-ORIGIN + tx0 * 256 * res, ORIGIN - ty0 * 256 * res, res, res)
    mosaic = np.zeros((H, W), np.uint8)
    to_m = Transformer.from_crs(crs, "EPSG:3857", always_xy=True)
    inv = ~dst_t
    for arr, tr in blocks:
        h, w = arr.shape
        xs, ys = [], []
        for c, r in ((0, 0), (w, 0), (0, h), (w, h)):
            X, Y = to_m.transform(*(tr * (c, r)))
            xs.append(X)
            ys.append(Y)
        c0, r0 = inv * (min(xs), max(ys))
        c1, r1 = inv * (max(xs), min(ys))
        c0, r0 = max(0, int(c0) - 2), max(0, int(r0) - 2)
        c1, r1 = min(W, int(c1) + 3), min(H, int(r1) + 3)
        if c1 <= c0 or r1 <= r0:
            continue
        tmp = np.zeros((r1 - r0, c1 - c0), np.uint8)
        reproject(arr, tmp, src_transform=tr, src_crs=crs,
                  dst_transform=dst_t * dst_t.translation(c0, r0), dst_crs="EPSG:3857",
                  resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
        np.maximum(mosaic[r0:r1, c0:c1], tmp, out=mosaic[r0:r1, c0:c1])
    tiles = {}
    level = mosaic
    for z in range(maxzoom, minzoom - 1, -1):
        f = 2 ** (maxzoom - z)
        bx0, by0 = tx0 // f, ty0 // f
        nty, ntx = level.shape[0] // 256, level.shape[1] // 256
        for j in range(nty):
            for i in range(ntx):
                tile = level[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256]
                if z < uebersicht_bis_zoom + 1 and z < maxzoom:
                    # Übersicht: nur tiefes Wasser, sonst wirkt alles überflutet
                    tile = np.where(tile >= uebersicht_ab_klasse, tile, 0).astype(np.uint8)
                if tile.any():
                    tiles[zxy_to_tileid(z, bx0 + i, by0 + j)] = _palette_png(np.ascontiguousarray(tile))
        if z > minzoom:
            h2, w2 = level.shape[0] // 2, level.shape[1] // 2
            level = level.reshape(h2, 2, w2, 2).max(axis=(1, 3))
    with open(out_path, "wb") as fh:
        w = Writer(fh)
        for tid in sorted(tiles):
            w.write_tile(tid, tiles[tid])
        e7 = lambda v: int(round(v * 1e7))  # noqa: E731
        w.finalize({
            "tile_type": TileType.PNG, "tile_compression": Compression.NONE,
            "min_zoom": minzoom, "max_zoom": maxzoom,
            "min_lon_e7": e7(bounds_ll[0]), "min_lat_e7": e7(bounds_ll[1]),
            "max_lon_e7": e7(bounds_ll[2]), "max_lat_e7": e7(bounds_ll[3]),
            "center_zoom": minzoom + 2, "center_lon_e7": e7((bounds_ll[0] + bounds_ll[2]) / 2),
            "center_lat_e7": e7((bounds_ll[1] + bounds_ll[3]) / 2)},
            {"name": out_path.stem, "description": "Starkregen-Spiegel – max. Wassertiefe (Klassen)"})
    return len(tiles)


def cell_key(x, y, size=1000):
    return f"{int(x // size)}_{int(y // size)}"


def split_features(fc_list, key_of):
    """Verteilt Features auf Zellen. key_of(feature) -> Zellschlüssel."""
    out = {}
    for fc in fc_list:
        for f in fc["features"]:
            out.setdefault(key_of(f), []).append(f)
    return out
