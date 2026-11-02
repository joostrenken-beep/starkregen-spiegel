"""OpenStreetMap-Daten: Abruf über Overpass und Umwandlung in GeoDataFrames."""
from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import requests
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = """[out:json][timeout:180];
(
  way["building"]({bbox});
  relation["building"]["type"="multipolygon"]({bbox});
  way["highway"]({bbox});
  way["area:highway"]({bbox});
  way["amenity"="parking"]({bbox});
  way["landuse"]({bbox});
  relation["landuse"]["type"="multipolygon"]({bbox});
  way["waterway"]({bbox});
  way["natural"="water"]({bbox});
  way["railway"]({bbox});
  nwr["addr:housenumber"]({bbox});
);
out geom;"""


def bbox_wgs84(bbox_utm, crs="EPSG:25832"):
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    xmin, ymin, xmax, ymax = bbox_utm
    xs, ys = zip(*[t.transform(x, y) for x, y in
                   [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]])
    return min(ys), min(xs), max(ys), max(xs)   # Overpass: S,W,N,E


def overpass_query_text(bbox_utm) -> str:
    s, w, n, e = bbox_wgs84(bbox_utm)
    return QUERY.format(bbox=f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}")


def fetch(bbox_utm, out_path: Path) -> Path:
    q = overpass_query_text(bbox_utm)
    last = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                r = requests.post(url, data={"data": q}, timeout=300,
                                  headers={"User-Agent": "starkregen-spiegel/0.1 (Pilotprojekt)"})
                if r.status_code == 200:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(r.content)
                    return out_path
                last = f"HTTP {r.status_code}"
            except requests.RequestException as ex:  # Netzfehler -> nächster Versuch
                last = str(ex)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass nicht erreichbar ({last}). Alternative: Abfrage aus "
                       f"docs/overpass_abfrage.txt auf https://overpass-turbo.eu ausführen, "
                       f"als 'Rohdaten (JSON)' exportieren und als {out_path} speichern.")


def _ring(geom):
    return [(p["lon"], p["lat"]) for p in geom]


def load(path: Path, crs="EPSG:25832") -> dict[str, gpd.GeoDataFrame]:
    """Liest Overpass-JSON ('out geom') und gibt Layer im Ziel-KBS zurück."""
    data = json.loads(Path(path).read_text())
    rows = {"buildings": [], "highways": [], "areas": [], "waterways": [], "railways": [],
            "addresses": []}
    for el in data["elements"]:
        tags = el.get("tags", {})
        geom = None
        if el["type"] == "way" and "geometry" in el:
            pts = _ring(el["geometry"])
            closed = len(pts) >= 4 and pts[0] == pts[-1]
            if closed and ("building" in tags or "landuse" in tags or "area:highway" in tags
                           or tags.get("amenity") == "parking" or tags.get("natural") == "water"
                           or tags.get("area") == "yes"):
                geom = Polygon(pts)
            elif len(pts) >= 2:
                geom = LineString(pts)
        elif el["type"] == "relation":
            outers = [LineString(_ring(m["geometry"])) for m in el.get("members", [])
                      if m.get("role") == "outer" and "geometry" in m]
            inners = [LineString(_ring(m["geometry"])) for m in el.get("members", [])
                      if m.get("role") == "inner" and "geometry" in m]
            if outers:
                outer = unary_union(list(polygonize(unary_union(outers))))
                if inners:
                    outer = outer.difference(unary_union(list(polygonize(unary_union(inners)))))
                geom = outer
        elif el["type"] == "node":
            geom = Point(el["lon"], el["lat"])
        if geom is None or geom.is_empty:
            continue
        rec = {"osm_id": f"{el['type'][0]}{el['id']}", "geometry": geom, **{
            k: tags.get(k) for k in ("building", "highway", "landuse", "amenity", "waterway",
                                     "tunnel", "bridge", "railway", "natural", "water",
                                     "area:highway", "name",
                                     "addr:street", "addr:housenumber", "addr:postcode",
                                     "addr:city")}}
        if "addr:housenumber" in tags:
            rows["addresses"].append({**rec, "geometry": geom.representative_point()
                                      if geom.geom_type != "Point" else geom})
        if "building" in tags and geom.geom_type in ("Polygon", "MultiPolygon"):
            rows["buildings"].append(rec)
        elif "highway" in tags and geom.geom_type == "LineString":
            rows["highways"].append(rec)
        elif "railway" in tags and geom.geom_type == "LineString":
            rows["railways"].append(rec)
        elif "waterway" in tags and geom.geom_type == "LineString":
            rows["waterways"].append(rec)
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            rows["areas"].append(rec)
    out = {}
    for k, v in rows.items():
        g = gpd.GeoDataFrame(v, geometry="geometry", crs="EPSG:4326") if v else \
            gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
        out[k] = g.to_crs(crs)
    return out
