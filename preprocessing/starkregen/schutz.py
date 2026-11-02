"""Schutzwürdigkeit von Gebäuden und Starkregen-Risiko.

Schutzwürdigkeit (S1–S4) – orientiert an
  * DWA-A 118 (2006), Tab. 3 "Empfohlene Überflutungshäufigkeiten":
    ländliche Gebiete 1 in 10 a, Wohngebiete 1 in 20 a, Stadtzentren/Industrie/
    Gewerbe 1 in 30 a, unterirdische Verkehrsanlagen/Unterführungen 1 in 50 a
  * DWA-M 119 (2016): Schadenspotenzial/Risiko = Gefährdung × Schadenspotenzial,
    besonders schutzbedürftig: Menschen mit eingeschränkter Selbstrettung
    (Kita, Schule, Pflege, Krankenhaus), Einsatzkräfte, kritische Infrastruktur,
    unterirdische Räume, Kulturgut.

Grundlage ausschließlich öffentliche Daten: ALKIS-Gebäudefunktion (gfk), Lage zur
Erdoberfläche (ofl), Bauart (baw), Grundfläche und die tatsächliche Nutzung des
Flurstücks (ALKIS). Keller sind in öffentlichen Daten NICHT enthalten.
Alle Regeln stehen in config/pilot.yaml (Abschnitt "risiko").
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np

KLASSEN = {1: "gering", 2: "mäßig", 3: "hoch", 4: "sehr hoch"}


def classify(buildings: gpd.GeoDataFrame, nutzung: gpd.GeoDataFrame, cfg: dict):
    """Rückgabe: (klasse int-Array 1..4, grund Liste[str]) in Reihenfolge von buildings."""
    rc = cfg["risiko"]["schutzwuerdigkeit"]
    n = len(buildings)
    klasse = np.full(n, int(rc.get("standard", 2)))
    grund = ["Standard (Gebäudefunktion ohne besondere Zuordnung)"] * n

    gfk = buildings.get("gfk", gpd.pd.Series([None] * n)).astype("Int64").to_numpy()
    gfk_bez = buildings.get("gfk__bez", buildings.get("building", gpd.pd.Series([""] * n))).fillna("").to_numpy()
    ofl = buildings.get("ofl__bez", gpd.pd.Series([None] * n)).fillna("").to_numpy()
    baw = buildings.get("baw__bez", gpd.pd.Series([None] * n)).fillna("").to_numpy()
    area = buildings.geometry.area.to_numpy()

    # Nutzung des Flurstücks am Gebäudeschwerpunkt
    pts = gpd.GeoDataFrame(geometry=buildings.geometry.representative_point(), crs=buildings.crs)
    j = gpd.sjoin(pts, nutzung[["tn__bez", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    tn = j["tn__bez"].reindex(range(n)).fillna("").to_numpy()

    def setk(mask, k, text, only_raise=False):
        for i in np.flatnonzero(mask):
            if only_raise and klasse[i] >= k:
                continue
            klasse[i] = k
            grund[i] = text

    # 1) Gebäudefunktion (ALKIS-Schlüssel), Reihenfolge = Priorität aufsteigend
    for regel in rc["gebaeudefunktion"]:
        codes = set(regel.get("gfk", []))
        m = np.array([c in codes if c is not None and c is not gpd.pd.NA else False for c in gfk])
        for i in np.flatnonzero(m):
            klasse[i] = regel["klasse"]
            grund[i] = f"{gfk_bez[i]}: {regel['text']}"

    # 2) kleine Gebäude allgemeiner Funktion = Nebengebäude (Schuppen, Carport …)
    nb = rc["nebengebaeude"]
    m = np.isin(gfk, nb["gfk"]) & (area < nb["max_flaeche_m2"])
    setk(m, nb["klasse"], f"Nebengebäude (< {nb['max_flaeche_m2']} m², {nb['text']})")
    wmax = nb.get("auf_wohnbauflaeche_max_m2", 0)
    m = np.isin(gfk, nb["gfk"]) & (gfk != 1000) & (area < wmax) & np.array([t.startswith("Wohnbaufläche") for t in tn])
    setk(m, nb["klasse"], f"Nebengebäude auf Wohnbaufläche (< {wmax} m², z. B. Garage/Schuppen)")
    setk(baw == "Offene Halle", rc.get("offene_halle_klasse", 1), "Offene Halle / Überdachung")

    # 3) Nutzung des Flurstücks hebt die Klasse (z. B. Wohngebäude auf Fläche "Soziales" = Pflegeheim)
    for regel in rc["flurstuecksnutzung"]:
        m = np.array([any(t.startswith(p) for p in regel["praefix"]) for t in tn]) & (area >= nb["max_flaeche_m2"])
        for i in np.flatnonzero(m):
            if klasse[i] < regel["klasse"]:
                klasse[i] = regel["klasse"]
                art = tn[i].split(", ", 1)[-1]
                grund[i] = f"{gfk_bez[i]} auf Fläche „{art}“: {regel['text']}"

    # 4) unterirdische Gebäude (Tiefgarage, Bunker …): A 118 – unterirdische Anlagen 1 in 50 a
    setk(ofl == "Unter der Erdoberfläche", 4, "Unterirdisches Gebäude (vgl. A 118: unterirdische Anlagen)")
    return klasse, grund


def hazard_level(depth, ring_id, n, amin, grenzen):
    """Gefährdungsstufe 0..len(grenzen) je Gebäude: höchste Tiefengrenze, die im
    Ring auf mindestens amin m² erreicht wird (Zellen 1 m²)."""
    m = ring_id > 0
    lvl = np.zeros(n + 1, int)
    for k, g in enumerate(grenzen, 1):
        a = np.bincount(ring_id[m], weights=(depth[m] >= g) * 1.0, minlength=n + 1)
        lvl[a >= amin] = k
    return lvl[1:]


def risk(gef, klasse, matrix):
    """Risikoklasse 0..3 aus Matrix[Gefährdung][Schutzwürdigkeit-1]."""
    M = np.array(matrix)
    return M[gef, klasse - 1]
