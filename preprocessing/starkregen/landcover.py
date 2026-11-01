"""Landbedeckung aus OSM rastern, Gebäude einbrennen, Durchlässe öffnen, Abfluss bilden."""
from __future__ import annotations

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage


def rasterize_buildings(buildings, transform, shape):
    """Gebäude-ID-Raster (0 = kein Gebäude, sonst Index+1 in `buildings`)."""
    if len(buildings) == 0:
        return np.zeros(shape, np.int32)
    shapes = ((g, i + 1) for i, g in enumerate(buildings.geometry))
    return rasterize(shapes, out_shape=shape, transform=transform, fill=0,
                     dtype="int32", all_touched=False)


def sealed_fraction(layers, transform, shape, cfg):
    """Versiegelungsgrad je Zelle (0..1), ohne Gebäude (die werden separat behandelt)."""
    vg = cfg["abfluss"]["versiegelungsgrad"]
    widths = cfg["abfluss"]["strassenbreite_m"]
    s = np.full(shape, float(vg.get("sonstiges", 0.0)))

    areas = layers["areas"]
    # 1) Landnutzung (grob)
    lu_shapes = []
    for g, lu in zip(areas.geometry, areas.get("landuse", [None] * len(areas))):
        if lu and f"landuse_{lu}" in vg:
            lu_shapes.append((g, vg[f"landuse_{lu}"]))
    if lu_shapes:
        lu = rasterize(lu_shapes, out_shape=shape, transform=transform, fill=-1, dtype="float64")
        s = np.where(lu >= 0, lu, s)

    # 2) Straßen (Achsen gepuffert) und Parkplätze / Straßenflächen = voll versiegelt
    sealed_shapes = []
    hw = layers["highways"]
    for g, t, tun in zip(hw.geometry, hw["highway"], hw.get("tunnel", [None] * len(hw))):
        if tun in ("yes", "building_passage"):
            continue
        w = widths.get(t)
        if w:
            sealed_shapes.append((g.buffer(w / 2, cap_style="flat"), 1))
    for g, am, ah in zip(areas.geometry, areas.get("amenity", [None] * len(areas)),
                         areas.get("area:highway", [None] * len(areas))):
        if am == "parking" or ah:
            sealed_shapes.append((g, 1))
    if sealed_shapes:
        m = rasterize(sealed_shapes, out_shape=shape, transform=transform, fill=0, dtype="uint8")
        s = np.where(m == 1, float(vg.get("strasse", 1.0)), s)

    # 3) Gewässerflächen: nicht versiegelt, aber auch keine Versickerung (wassergesättigt)
    water_shapes = [(g, 1) for g, nat in zip(areas.geometry, areas.get("natural", [None] * len(areas)))
                    if nat == "water"]
    water = np.zeros(shape, bool)
    if water_shapes:
        water = rasterize(water_shapes, out_shape=shape, transform=transform, fill=0,
                          dtype="uint8") == 1
    return np.clip(s, 0, 1), water


def burn_culverts(dem, waterways, transform, search=3):
    """Öffnet Durchlässe (OSM waterway + tunnel=culvert): Zellen entlang der Leitung
    werden auf eine lineare Verbindung der Sohlhöhen an Ein- und Auslauf abgesenkt.
    So wirkt ein Straßendamm über einem Durchlass nicht mehr als Staudamm."""
    dem = dem.copy()
    if len(waterways) == 0:
        return dem, 0
    inv = ~transform
    n = 0
    nr, nc = dem.shape
    for g, tun in zip(waterways.geometry, waterways.get("tunnel", [None] * len(waterways))):
        if tun != "culvert" or g.geom_type != "LineString":
            continue
        pts = []
        for d in np.arange(0, g.length + 0.5, 0.5):
            p = g.interpolate(d)
            c, r = inv * (p.x, p.y)
            pts.append((int(r), int(c)))
        pts = [(r, c) for r, c in pts if 0 <= r < nr and 0 <= c < nc]
        if len(pts) < 2:
            continue

        def low(rc):
            r, c = rc
            win = dem[max(0, r - search):r + search + 1, max(0, c - search):c + search + 1]
            return np.nanmin(win)
        z0, z1 = low(pts[0]), low(pts[-1])
        for k, (r, c) in enumerate(pts):
            z = z0 + (z1 - z0) * k / (len(pts) - 1)
            if dem[r, c] > z:
                dem[r, c] = z
        n += 1
    return dem, n


def building_ring(bid, width=1):
    """Ring-Zellen um Gebäude (außerhalb), jeweils dem nächstgelegenen Gebäude zugeordnet."""
    inside = bid > 0
    if not inside.any():
        return np.zeros_like(bid)
    dist, (ir, ic) = ndimage.distance_transform_edt(~inside, return_indices=True)
    ring = (~inside) & (dist <= width + 0.5)
    out = np.zeros_like(bid)
    out[ring] = bid[ir[ring], ic[ring]]
    return out


def runoff(P_mm, T_h, sealed, roof, ring_id, bid, water, cfg):
    """Effektiver Oberflächenabfluss je Zelle (m) für einen Blockregen.

    Rückgabe: runoff_m (2D), Bilanz-Dict (m³ bei 1 m² Zellen)
    """
    a = cfg["abfluss"]
    P = P_mm / 1000.0
    eff_sealed = max(0.0, P - a["versiegelt"]["benetzungsverlust_mm"] / 1000
                     - a["kanal"]["abschlag_mm_h"] * T_h / 1000)
    sewer = min(max(0.0, P - a["versiegelt"]["benetzungsverlust_mm"] / 1000),
                a["kanal"]["abschlag_mm_h"] * T_h / 1000)
    eff_perv = max(0.0, P - a["unversiegelt"]["anfangsverlust_mm"] / 1000
                   - a["unversiegelt"]["infiltration_mm_h"] * T_h / 1000)
    r = sealed * eff_sealed + (1 - sealed) * eff_perv
    r = np.where(water, P, r)          # Regen auf Wasserflächen fließt vollständig ab
    sewer_vol = float((sealed * sewer).sum() + roof.sum() * sewer)

    # Dächer: Überschuss gleichmäßig auf den Ring um das jeweilige Gebäude
    r = np.where(roof, 0.0, r)
    nb = int(bid.max())
    if nb > 0 and eff_sealed > 0:
        roof_vol = np.bincount(bid[roof].ravel(), minlength=nb + 1) * eff_sealed
        ring_cnt = np.bincount(ring_id[ring_id > 0].ravel(), minlength=nb + 1)
        add = np.zeros(nb + 1)
        ok = ring_cnt > 0
        add[ok] = roof_vol[ok] / ring_cnt[ok]
        r = r + np.where(ring_id > 0, add[ring_id], 0.0)
    rain_vol = float(P * r.size)
    return r, {"niederschlag_m3": rain_vol, "kanal_m3": sewer_vol,
               "oberflaechenabfluss_m3": float(r.sum())}
