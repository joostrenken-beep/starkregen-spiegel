"""ALKIS (LGLN Open Data, Landkreis-Geopackage) als amtliche Datengrundlage.

Layer: gebaeude (Grundrisse + Straße/Hausnummer), nutzung (tatsächliche Nutzung),
bauwerke_f (Brücken, Durchlässe, Unterführungen …), flurstuecke.

Liefert dieselben Layer-Namen wie osm.load(), damit Pipeline und
Plausibilitätsbericht unverändert funktionieren.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.features import rasterize

WATER_PREFIX = ("Fliessgewässer", "Stehendes Gewässer", "Hafenbecken", "Sumpf")


def find_gpkg(folder: Path):
    files = sorted(Path(folder).glob("*.gpkg"))
    return files[0] if files else None


def load(path: Path, bbox, buffer=50):
    b = (bbox[0] - buffer, bbox[1] - buffer, bbox[2] + buffer, bbox[3] + buffer)
    geb = gpd.read_file(path, layer="gebaeude", bbox=b)
    nut = gpd.read_file(path, layer="nutzung", bbox=b)
    bw = gpd.read_file(path, layer="bauwerke_f", bbox=b)
    fs = gpd.read_file(path, layer="flurstuecke", bbox=b)

    # Gebäude: aufgeständerte und unterirdische nicht einbrennen (Wasser fließt drunter/drüber)
    ofl = geb["ofl__bez"].fillna("")
    geb = geb[~ofl.isin(["Aufgeständert", "Unter der Erdoberfläche"])].copy()
    geb["osm_id"] = geb["objid"]
    geb["building"] = geb["gfk__bez"]
    geb = geb.reset_index(drop=True)

    tn = nut["tn__bez"].fillna("")
    is_water = tn.str.startswith(WATER_PREFIX)
    areas = nut.assign(
        natural=np.where(is_water, "water", None),
        landuse=np.where(tn.str.contains("Speicherbecken|Klärwerk"), "basin", None),
        amenity=np.where(tn.str.startswith("Platz, Parkplatz"), "parking", None),
    )

    # "Straßen" als Flächen (für Plausibilitätsbericht)
    road = tn.str.startswith(("Strassenverkehr", "Platz", "Weg"))
    highways = nut[road & ~tn.str.contains("Begleitfläche")].assign(
        highway=np.where(tn[road & ~tn.str.contains("Begleitfläche")].str.startswith("Weg"),
                         "footway", "residential"), tunnel=None, bridge=None)

    bwf = bw["bwf__bez"].fillna("")
    bridges = bw[bwf.str.contains("Bruecke|Tunnel|Unterfuehrung")].assign(
        highway="residential", bridge="yes", tunnel=None)
    highways = pd.concat([highways, bridges], ignore_index=True)

    culverts = bw[bwf == "Durchlass"].assign(waterway="culvert", tunnel="culvert")
    gew = nut[tn.str.startswith("Fliessgewässer")].assign(waterway="stream", tunnel=None)
    waterways = pd.concat([gew, culverts], ignore_index=True)

    railways = nut[tn.str.startswith("Bahnverkehr")].assign(railway="rail", bridge=None)

    addr = geb.dropna(subset=["hnr"]).copy()
    addr["addr:street"] = addr["bez"]
    addr["addr:housenumber"] = addr["hnr"]
    addr["geometry"] = addr.geometry.representative_point()

    return {"buildings": geb, "areas": gpd.GeoDataFrame(areas, geometry="geometry", crs=nut.crs),
            "highways": gpd.GeoDataFrame(highways, geometry="geometry", crs=nut.crs),
            "waterways": gpd.GeoDataFrame(waterways, geometry="geometry", crs=nut.crs),
            "railways": gpd.GeoDataFrame(railways, geometry="geometry", crs=nut.crs),
            "addresses": addr, "flurstuecke": fs, "culverts": culverts}


def sealed_fraction(layers, transform, shape, cfg):
    """Versiegelungsgrad (ohne Gebäude) aus der tatsächlichen Nutzung.

    Zuordnung in config: abfluss.versiegelung_alkis = Liste [Präfix, Wert];
    der erste passende Präfix gewinnt.
    """
    rules = cfg["abfluss"]["versiegelung_alkis"]
    areas = layers["areas"]
    vals = []
    for t in areas["tn__bez"].fillna(""):
        v = None
        for prefix, val in rules:
            if t.startswith(prefix):
                v = float(val)
                break
        vals.append(0.0 if v is None else v)
    shapes_ = [(g, v) for g, v in zip(areas.geometry, vals)]
    s = rasterize(shapes_, out_shape=shape, transform=transform, fill=0.0, dtype="float64") \
        if shapes_ else np.zeros(shape)
    wshapes = [(g, 1) for g, n in zip(areas.geometry, areas["natural"]) if n == "water"]
    water = rasterize(wshapes, out_shape=shape, transform=transform, fill=0, dtype="uint8") == 1 \
        if wshapes else np.zeros(shape, bool)
    return np.clip(s, 0, 1), water


def open_culverts(dem, culverts, transform, ring=6.0):
    """Durchlass-Bauwerke (Flächen): Zellen im Bauwerk auf die tiefste Geländehöhe
    im Umring absenken – das Wasser kann den Damm dort passieren."""
    dem = dem.copy()
    n = 0
    for g in culverts.geometry:
        inner = rasterize([(g.buffer(1.0), 1)], out_shape=dem.shape, transform=transform,
                          fill=0, dtype="uint8") == 1
        outer = rasterize([(g.buffer(ring), 1)], out_shape=dem.shape, transform=transform,
                          fill=0, dtype="uint8") == 1
        if not inner.any():
            continue
        z = np.nanmin(dem[outer])
        dem[inner] = np.minimum(dem[inner], z)
        n += 1
    return dem, n


def infer_culverts(layers, max_gap=40.0):
    """Vermutete Durchlässe: ein ALKIS-Gewässer endet an einer Straße/Bahn und geht
    auf der anderen Seite innerhalb von max_gap Metern weiter.

    Rückgabe: GeoDataFrame mit Verbindungslinien (tunnel='culvert'), Spalten
    laenge_m, kreuzt (Nutzungsart der gequerten Fläche).
    """
    from shapely.geometry import LineString
    from shapely.ops import nearest_points

    areas = layers["areas"]
    tn = areas["tn__bez"].fillna("")
    streams = areas[tn.str.startswith("Fliessgewässer")].reset_index(drop=True)
    barrier = areas[tn.str.startswith(("Strassenverkehr", "Bahnverkehr", "Platz", "Weg"))]
    bar_union = barrier.geometry.union_all() if len(barrier) else None
    # zusammenhängende Gewässerstücke bilden (berührende Polygone vereinen)
    merged = streams.geometry.buffer(0.3).union_all()
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    rows = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            d = a.distance(b)
            if d < 0.5 or d > max_gap:
                continue
            p, q = nearest_points(a, b)
            line = LineString([p, q])
            if bar_union is None or not line.intersects(bar_union):
                continue
            # extend line slightly into both stream parts
            ext = LineString([
                (p.x - (q.x - p.x) / max(d, 1e-6) * 3, p.y - (q.y - p.y) / max(d, 1e-6) * 3),
                (q.x + (q.x - p.x) / max(d, 1e-6) * 3, q.y + (q.y - p.y) / max(d, 1e-6) * 3)])
            hit = barrier[barrier.intersects(line)]["tn__bez"].iloc[0]
            rows.append({"geometry": ext, "laenge_m": round(d, 1), "kreuzt": hit, "tunnel": "culvert"})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=areas.crs) if rows else \
        gpd.GeoDataFrame({"geometry": [], "laenge_m": [], "kreuzt": [], "tunnel": []},
                         geometry="geometry", crs=areas.crs)


def connect_culverts(layers, search=60.0):
    """ALKIS-Durchlässe (oft nur ein Teil des Damms erfasst) zu durchgehenden
    Verbindungslinien ergänzen: nächstes Gewässerstück vor dem Bauwerk → Bauwerk →
    nächstes Gewässerstück auf der gegenüberliegenden Seite."""
    from shapely.geometry import LineString
    from shapely.ops import nearest_points

    areas = layers["areas"]
    streams = areas[areas["tn__bez"].fillna("").str.startswith("Fliessgewässer")]
    merged = streams.geometry.buffer(0.3).union_all() if len(streams) else None
    parts = (list(merged.geoms) if hasattr(merged, "geoms") else [merged]) if merged is not None else []
    rows = []
    for g in layers["culverts"].geometry:
        c = g.centroid
        cand = sorted([(p.distance(g), p) for p in parts if p.distance(g) <= search], key=lambda t: t[0])
        if len(cand) < 2:
            continue
        a = nearest_points(cand[0][1], c)[0]
        va = np.array([c.x - a.x, c.y - a.y])
        best = None
        for _, p in cand[1:]:
            b = nearest_points(p, c)[0]
            vb = np.array([b.x - c.x, b.y - c.y])
            if np.dot(va, vb) > 0:          # liegt auf der anderen Seite
                best = b
                break
        if best is None:
            continue
        rows.append({"geometry": LineString([(a.x, a.y), (c.x, c.y), (best.x, best.y)]),
                     "laenge_m": round(a.distance(c) + c.distance(best), 1),
                     "kreuzt": "ALKIS-Durchlass", "tunnel": "culvert"})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=areas.crs) if rows else \
        gpd.GeoDataFrame({"geometry": [], "laenge_m": [], "kreuzt": [], "tunnel": []},
                         geometry="geometry", crs=areas.crs)


def corridor_mask(layers, extra_lines, transform, shape, stream_buffer=2.0, culvert_buffer=25.0,
                  line_buffer=2.0):
    """Korridor, in dem Dämme durchstochen werden dürfen: Fließgewässer (ALKIS),
    Durchlass-Bauwerke (großzügig, weil oft nur ein Teil des Damms erfasst ist)
    und vermutete Durchlass-Linien."""
    areas = layers["areas"]
    shapes_ = [(g.buffer(stream_buffer), 1) for g, t in zip(areas.geometry, areas["tn__bez"].fillna(""))
               if t.startswith("Fliessgewässer")]
    shapes_ += [(g.buffer(culvert_buffer), 1) for g in layers["culverts"].geometry]
    shapes_ += [(g.buffer(line_buffer), 1) for g in extra_lines.geometry]
    if not shapes_:
        return np.zeros(shape, bool)
    return rasterize(shapes_, out_shape=shape, transform=transform, fill=0, dtype="uint8") == 1


def infiltration_raster(gpkg, bbox, transform, shape, cfg):
    """Versickerungsrate (mm/h) je Zelle aus der ALKIS-Bodenschätzung.

    Bodenart (erster passender Präfix der Tabelle) × Faktor für die Wasserstufe
    (Grünland: hoch anstehendes Grundwasser). Ohne Bodenschätzung (bebaute Flächen):
    Einheitswert abfluss.unversiegelt.infiltration_mm_h.
    """
    vc = cfg["abfluss"]["versickerung_boden"]
    default = float(cfg["abfluss"]["unversiegelt"]["infiltration_mm_h"])
    bs = gpd.read_file(gpkg, layer="bodenschaetzung", bbox=tuple(bbox),
                       columns=["bez_boa", "bez_wav"])
    out = np.full(shape, default, dtype=np.float32)
    if len(bs) == 0:
        return out, 0.0
    rates = []
    for boa, wav in zip(bs["bez_boa"].fillna(""), bs["bez_wav"].fillna("")):
        r = default
        for prefix, val in vc["bodenart"]:
            if boa.startswith(prefix):
                r = float(val)
                break
        for stufe, fak in vc["wasserstufe_faktor"].items():
            if wav.replace(" ", "").startswith(f"Wasserstufe({stufe}"):
                r *= float(fak)
                break
        rates.append(r)
    shapes_ = [(g, v) for g, v in zip(bs.geometry, rates) if g is not None]
    ras = rasterize(shapes_, out_shape=shape, transform=transform, fill=-1.0, dtype="float32")
    cover = ras >= 0
    out[cover] = ras[cover]
    return out, float(cover.mean())
