"""Plausibilitätsprüfung ohne Ortskenntnis.

Prüft das Ergebnis gegen unabhängige Informationen aus OSM und erzeugt für die
größten Senken je einen kleinen Steckbrief mit Bildausschnitt und Links zu
Luftbild/Karte, damit man jede Senke am Bildschirm einordnen kann.

Erwartungen (fachlich):
  * Große Senken liegen bevorzugt an Gewässern, Regenrückhaltebecken, Gräben,
    Unterführungen und Straßentiefpunkten – nicht mitten in Gärten.
  * Die stärksten Fließwege folgen Gräben, Bächen und Straßen.
  * Senken direkt an Bahn- oder Straßendämmen mit Gewässer daneben deuten auf
    einen fehlenden Durchlass hin.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pyproj import Transformer
from rasterio.features import shapes
from scipy import ndimage
from shapely.geometry import shape

from .preview import DEPTH_CMAP, hillshade

KATEGORIEN = {
    "gewaesser": "Gewässer/Becken (erwartet – DGM zeigt Wasserspiegel bzw. Sohle)",
    "unterfuehrung": "Unterführung/Brücke (erwartet, oft kritisch)",
    "damm": "Senke an Bahn-/Straßendamm mit Gewässer → fehlender Durchlass? PRÜFEN",
    "strasse": "Straße/Platz (plausibel: Straßentiefpunkt)",
    "bebauung": "Garten/Hof/Gebäudenähe (plausibel, aber Luftbild prüfen)",
    "frei": "Freifläche/Acker/Wiese (plausibel: Geländemulde)",
}


def _sink_polygons(sink_depth, transform, min_depth=0.10, min_area=50):
    lab, n = ndimage.label(sink_depth > min_depth, structure=np.ones((3, 3)))
    if n == 0:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry"), lab
    idx = np.arange(1, n + 1)
    area = ndimage.sum(np.ones_like(sink_depth), lab, idx)
    vol = ndimage.sum(sink_depth, lab, idx)
    dmax = ndimage.maximum(sink_depth, lab, idx)
    keep = set(idx[area >= min_area].tolist())
    polys = {}
    for geom, val in shapes(lab.astype(np.int32), mask=lab > 0, transform=transform):
        v = int(val)
        if v in keep:
            polys.setdefault(v, []).append(shape(geom))
    rows = []
    for v, gs in polys.items():
        from shapely.ops import unary_union
        rows.append({"senke": v, "flaeche_m2": float(area[v - 1]), "volumen_m3": float(vol[v - 1]),
                     "max_tiefe_m": float(dmax[v - 1]), "geometry": unary_union(gs)})
    return gpd.GeoDataFrame(rows, geometry="geometry"), lab


def _classify(poly, layers):
    def near(gdf, dist, cond=None):
        if gdf is None or len(gdf) == 0:
            return gdf.iloc[0:0] if gdf is not None else None
        g = gdf if cond is None else gdf[cond(gdf)]
        return g[g.geometry.distance(poly) <= dist]

    areas, hw, ww, rw, bld = (layers.get(k) for k in ("areas", "highways", "waterways", "railways", "buildings"))
    water_area = near(areas, 0, lambda d: (d["natural"] == "water") | d["landuse"].isin(["basin", "reservoir"]))
    w_lines = near(ww, 3)
    if (water_area is not None and len(water_area)) or (w_lines is not None and len(w_lines)):
        # Senke an Damm? (Bahn oder Hauptstraße direkt am Rand, Gewässer daneben)
        dam_rail = near(rw, 8)
        dam_road = near(hw, 8, lambda d: d["highway"].isin(["primary", "secondary", "trunk", "motorway"]))
        culv = near(ww, 15, lambda d: d["tunnel"] == "culvert")
        if ((dam_rail is not None and len(dam_rail)) or (dam_road is not None and len(dam_road))) \
                and (culv is None or len(culv) == 0) and poly.area > 300:
            return "damm"
        return "gewaesser"
    bridges = []
    for g in (rw, hw):
        b = near(g, 5, lambda d: d["bridge"].notna() & (d["bridge"] != "no")) if g is not None and "bridge" in g else None
        if b is not None and len(b):
            bridges.append(b)
    roads_in = near(hw, 0, lambda d: ~d["highway"].isin(["footway", "path", "track", "steps"]))
    if bridges and roads_in is not None and len(roads_in):
        return "unterfuehrung"
    if roads_in is not None and len(roads_in):
        return "strasse"
    b = near(bld, 3)
    if b is not None and len(b):
        return "bebauung"
    return "frei"


def report(out_dir: Path, dem, sink_depth, flow_volume, depth, building_mask, transform, crs,
           layers: dict | None, top_n=15, scenario_label=""):
    out_dir = Path(out_dir)
    pdir = out_dir / "plausi"
    pdir.mkdir(parents=True, exist_ok=True)
    sinks, lab = _sink_polygons(sink_depth, transform)
    sinks = sinks.set_crs(crs) if len(sinks) else sinks
    lines = [f"# Plausibilitätsbericht {scenario_label}", ""]

    total_vol = float(sinks["volumen_m3"].sum()) if len(sinks) else 0.0
    lines += [f"Senken > 10 cm tief und > 50 m²: **{len(sinks)}**, Gesamtvolumen "
              f"**{total_vol:,.0f} m³**.", ""]

    # --- Klassifizierung -----------------------------------------------------
    if layers is not None and len(sinks):
        sinks["kategorie"] = [_classify(p, layers) for p in sinks.geometry]
        tab = sinks.groupby("kategorie").agg(anzahl=("senke", "size"), volumen_m3=("volumen_m3", "sum"))
        lines += ["## Wo liegen die Senken?", "", "| Kategorie | Anzahl | Volumen m³ | Anteil Volumen |",
                  "|---|---:|---:|---:|"]
        for k, row in tab.sort_values("volumen_m3", ascending=False).iterrows():
            lines.append(f"| {KATEGORIEN[k]} | {int(row.anzahl)} | {row.volumen_m3:,.0f} | "
                         f"{row.volumen_m3 / total_vol * 100:.0f} % |")
        lines.append("")
    else:
        sinks["kategorie"] = "frei"

    # --- Fließwege vs. Gewässer/Straßen --------------------------------------
    if layers is not None:
        thr = np.nanpercentile(flow_volume[flow_volume > 0], 99) if (flow_volume > 0).any() else 0
        strong = (flow_volume >= thr) & (depth < 0.3)
        lines_geo = []
        for k in ("waterways", "highways"):
            g = layers.get(k)
            if g is not None and len(g):
                lines_geo.append(g.geometry)
        if lines_geo:
            from rasterio.features import rasterize
            geoms = [(geom.buffer(5), 1) for gs in lines_geo for geom in gs]
            near_mask = rasterize(geoms, out_shape=dem.shape, transform=transform, fill=0, dtype="uint8") == 1
            share = float(near_mask[strong].mean() * 100) if strong.any() else float("nan")
            base = float(near_mask.mean() * 100)
            lines += ["## Folgen die Hauptfließwege Gräben, Bächen und Straßen?", "",
                      f"Anteil der stärksten 1 % Fließweg-Zellen im 5-m-Band um Gewässer/Straßen: "
                      f"**{share:.0f} %** (Flächenanteil dieses Bandes am Gebiet: {base:.0f} %).",
                      "Deutlich höher als der Flächenanteil = Fließwege folgen erwartungsgemäß "
                      "Gräben und Straßen.", ""]

    # --- Steckbriefe der größten Senken ---------------------------------------
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    top = sinks.sort_values("volumen_m3", ascending=False).head(top_n) if len(sinks) else sinks
    lines += [f"## Die {len(top)} größten Senken – bitte im Luftbild ansehen", "",
              "| Nr | Kategorie | Volumen m³ | max. Tiefe m | Fläche m² | Luftbild | Karte |",
              "|---:|---|---:|---:|---:|---|---|"]
    feats = []
    inv = ~transform
    for i, (_, s) in enumerate(top.iterrows(), 1):
        c = s.geometry.representative_point()
        lon, lat = to_wgs.transform(c.x, c.y)
        gm = f"https://www.google.com/maps/@{lat:.6f},{lon:.6f},120m/data=!3m1!1e3"
        om = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=19/{lat:.6f}/{lon:.6f}"
        lines.append(f"| {i} | {s.kategorie} | {s.volumen_m3:,.0f} | {s.max_tiefe_m:.2f} | "
                     f"{s.flaeche_m2:,.0f} | [Luftbild]({gm}) | [OSM]({om}) |")
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"nr": i, "kategorie": s.kategorie, "volumen_m3": round(s.volumen_m3),
                                     "max_tiefe_m": round(s.max_tiefe_m, 2)}})
        # Bildausschnitt 150 × 150 m
        col, row = inv * (c.x, c.y)
        r0, c0 = max(0, int(row) - 75), max(0, int(col) - 75)
        r1, c1 = min(dem.shape[0], r0 + 150), min(dem.shape[1], c0 + 150)
        fig, ax = plt.subplots(figsize=(4, 4), constrained_layout=True)
        ax.imshow(hillshade(dem[r0:r1, c0:c1]), cmap="gray")
        ax.imshow(np.ma.masked_where(~building_mask[r0:r1, c0:c1], np.ones((r1 - r0, c1 - c0))),
                  cmap="autumn", alpha=0.45)
        ax.imshow(np.ma.masked_less(depth[r0:r1, c0:c1], 0.02), cmap=DEPTH_CMAP, vmin=0, vmax=1)
        ax.set_title(f"Senke {i}: {s.kategorie}, {s.volumen_m3:,.0f} m³", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.savefig(pdir / f"senke_{i:02d}.png", dpi=90)
        plt.close(fig)
    lines += ["", "Bildausschnitte: `plausi/senke_NN.png` (150 × 150 m, Wassertiefe des Szenarios).", "",
              "### Legende Kategorien", ""] + [f"- **{k}**: {v}" for k, v in KATEGORIEN.items()]
    (out_dir / "plausibilitaet.md").write_text("\n".join(lines) + "\n")
    (out_dir / "senken_top.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return sinks
