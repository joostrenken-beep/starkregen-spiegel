"""Meilenstein 3: Grundstücks-Steckbrief je Gebäude.

Baut auf den fertigen Blockergebnissen auf (keine neue Hydraulik):
  - Gebäudehöhe        Median von DOM1 − DGM1 im Grundriss (Rand 1 m ausgespart)
  - Flurstück          ALKIS-Flurstück, in dem das Gebäude liegt
  - Wasser auf dem Grundstück   größte Tiefe und nasse Fläche außerhalb der Gebäude
  - Einzugsgebiet      alle Flächen, deren Wasser über das Gelände zum Gebäude fließt
                       (D8 auf dem Geländemodell mit eingebrannten Gebäuden, Senken
                       gedanklich voll = Starkregen-Fall)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd
from rasterio.features import rasterize, shapes
from scipy import ndimage
from shapely.geometry import shape as shp_shape, Polygon, MultiPolygon
from shapely.ops import unary_union

from . import hydro
from .landcover import building_ring

ALKIS_SKIP = ["Aufgeständert", "Unter der Erdoberfläche"]


def read_layer(gpkg, layer, bbox, columns=None):
    g = gpd.read_file(gpkg, layer=layer, bbox=tuple(bbox), columns=columns)
    return g.reset_index(drop=True)


def _raster(geoms, transform, shape, values=None):
    if len(geoms) == 0:
        return np.zeros(shape, np.int32)
    vals = values if values is not None else range(1, len(geoms) + 1)
    return rasterize(((g, int(v)) for g, v in zip(geoms, vals)), out_shape=shape, transform=transform,
                     fill=0, dtype="int32")


# ---------------------------------------------------------------------------
def building_heights(geoms, dom, dgm, transform, default=np.nan):
    """Median der Objekthöhe (DOM − DGM) im Grundriss; Randzellen werden ausgespart,
    damit Dachüberstände/Nachbarflächen nicht mitzählen."""
    lab = _raster(list(geoms), transform, dom.shape)
    lo = ndimage.grey_erosion(lab, size=3)
    hi = ndimage.grey_dilation(lab, size=3)
    inner = (lab > 0) & (lo == lab) & (hi == lab)
    nd = dom - dgm
    out = np.full(len(geoms), default)
    for use in (inner, lab > 0):                  # kleine Gebäude: ganzer Grundriss
        m = use & np.isfinite(nd)
        if not m.any():
            continue
        s = pd.Series(nd[m]).groupby(lab[m]).median()
        idx = s.index.to_numpy() - 1
        miss = np.isnan(out[idx])
        out[idx[miss]] = s.to_numpy()[miss]
    return out


# ---------------------------------------------------------------------------
def parcel_stats(parcels, buildings_all, depth, transform):
    """Teilergebnisse je Flurstück auf einem Raster (Blockkern): größte Tiefe (m)
    und Flächen ≥10 cm / ≥30 cm (m²) außerhalb von Gebäuden.
    depth: dict sid -> 2D-Array. Rückgabe: dict objid -> {sid: [max, a10, a30]}."""
    shape = next(iter(depth.values())).shape
    lab = _raster(list(parcels.geometry), transform, shape)
    bmask = _raster(list(buildings_all.geometry), transform, shape) > 0
    lab = np.where(bmask, 0, lab)
    n = len(parcels)
    out = {}
    m = lab > 0
    L = lab[m]
    per = {}
    for sid, d in depth.items():
        v = np.nan_to_num(d[m], nan=0.0)
        mx = np.zeros(n + 1)
        np.maximum.at(mx, L, v)
        a10 = np.bincount(L, weights=(v >= 0.10).astype(float), minlength=n + 1)
        a30 = np.bincount(L, weights=(v >= 0.30).astype(float), minlength=n + 1)
        per[sid] = (mx, a10, a30)
    present = np.unique(L)
    for k in present:
        oid = parcels["objid"].iat[k - 1]
        out[oid] = {sid: [float(per[sid][0][k]), float(per[sid][1][k]), float(per[sid][2][k])] for sid in depth}
    return out


def merge_parcel_stats(acc, part):
    for oid, v in part.items():
        if oid not in acc:
            acc[oid] = v
            continue
        for sid, (mx, a10, a30) in v.items():
            o = acc[oid][sid]
            acc[oid][sid] = [max(o[0], mx), o[1] + a10, o[2] + a30]
    return acc


def parcel_of(buildings, parcels):
    """objid des Flurstücks, in dem der Gebäudeschwerpunkt (repräsentativer Punkt) liegt."""
    pts = gpd.GeoDataFrame(geometry=buildings.geometry.representative_point(), crs=buildings.crs)
    j = gpd.sjoin(pts, parcels[["objid", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    return j["objid"].reindex(range(len(buildings))).tolist()


# ---------------------------------------------------------------------------
def catchments(dgm, transform, buildings_all, core_idx, parcel_lab, own_parcel_lab,
               burn=5.0, ring=2, cap=3_000_000, min_extern_m2=100.0, simplify_m=1.5, log=None):
    """Einzugsgebiet je Gebäude (Index in buildings_all aus core_idx).

    parcel_lab: Flurstücksnummer je Zelle (0 = keines); own_parcel_lab[i]: Nummer des
    eigenen Flurstücks. Rückgabe je Gebäude: (Fläche m², Anteil außerhalb des eigenen
    Grundstücks, abgeschnitten?, Polygon in Rasterkoordinaten des KBS oder None)."""
    valid = np.isfinite(dgm)
    bid = _raster(list(buildings_all.geometry), transform, dgm.shape)
    z = np.where(bid > 0, dgm + burn, dgm)
    z = np.where(valid, z, np.nan)
    filled = hydro.fill_epsilon(z, valid)
    down = hydro.d8_down(filled, valid)
    del filled, z
    ptr, idx = hydro.children_csr(down)
    del down
    rid = building_ring(bid, width=ring)
    nr, nc = dgm.shape
    cellarea = abs(transform.a * transform.e)
    # Startzellen je Gebäude: Grundriss + Ring
    sel = (bid > 0) | (rid > 0)
    owner = np.where(bid > 0, bid, rid)[sel]
    cells = np.flatnonzero(sel.ravel())
    order = np.argsort(owner, kind="stable")
    owner, cells = owner[order], cells[order]
    bounds = np.searchsorted(owner, np.arange(0, len(buildings_all) + 2))
    stamp = np.zeros(nr * nc, dtype=np.int32)
    pl = parcel_lab.ravel()
    res = {}
    for t, i in enumerate(core_idx):
        k = i + 1
        st = cells[bounds[k]:bounds[k + 1]]
        if len(st) == 0:
            continue
        up, trunc = hydro.upstream(st, ptr, idx, stamp, t + 1, cap)
        rr, cc = up // nc, up % nc
        trunc = bool(trunc or (rr.min() == 0) or (cc.min() == 0) or (rr.max() == nr - 1) or (cc.max() == nc - 1))
        area = len(up) * cellarea
        own = own_parcel_lab[t]
        ext = float(np.mean(pl[up] != own)) if own > 0 else 1.0
        extern = area - len(st) * cellarea
        poly = None
        if extern >= min_extern_m2:
            r0, r1, c0, c1 = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
            mask = np.zeros((r1 - r0, c1 - c0), np.uint8)
            mask[rr - r0, cc - c0] = 1
            tr = transform * transform.translation(c0, r0)
            parts = [shp_shape(g) for g, v in shapes(mask, mask=mask.astype(bool), transform=tr) if v == 1]
            g = unary_union(parts)
            if isinstance(g, MultiPolygon):
                g = max(g.geoms, key=lambda p: p.area)
            if isinstance(g, Polygon):
                holes = [h for h in g.interiors if Polygon(h).area > 200]
                g = Polygon(g.exterior, holes).simplify(simplify_m, preserve_topology=True)
                poly = g
        res[i] = (area, ext, trunc, poly)
        if log and (t + 1) % 3000 == 0:
            log(f"    Einzugsgebiete: {t + 1}/{len(core_idx)}")
    return res
