"""Bequeme Python-Schnittstelle zum hydrologischen Kern (hydro.py)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import hydro


@dataclass
class Hierarchy:
    """Szenariounabhängige Geländeanalyse (einmal rechnen, für alle Regen nutzen)."""
    shape: tuple
    dem: np.ndarray            # eingebranntes DGM (2D)
    valid: np.ndarray
    label: np.ndarray          # flach
    key: np.ndarray            # flach
    parent_cell: np.ndarray    # flach, Fließbaum
    n_labels: int
    cap: np.ndarray
    own_cap: np.ndarray
    zspill: np.ndarray
    parent: np.ndarray
    sibling: np.ndarray
    link: np.ndarray
    child_a: np.ndarray
    child_b: np.ndarray
    n_nodes: int
    cell_area: float

    def root_of_leaf(self) -> np.ndarray:
        """Wurzelknoten je Einzelsenke (oberste Senke, die zum Rand/Unterlieger überläuft)."""
        return hydro.root_nodes(self.parent, self.n_nodes)[: self.n_labels].copy()

    def sink_depth_full(self) -> np.ndarray:
        """Senkentiefe, wenn alle Senken bis zum Überlauf gefüllt wären (m)."""
        root = self.root_of_leaf()
        zs = self.zspill[root]
        zs[0] = -np.inf
        filled = hydro.filled_dem(self.dem.ravel(), self.key, self.label, self.n_labels, zs)
        d = filled - self.dem.ravel()
        d[~self.valid.ravel()] = 0
        return d.reshape(self.shape)


def analyse_terrain(dem: np.ndarray, valid: np.ndarray, no_seed: np.ndarray,
                    cell_area: float = 1.0) -> Hierarchy:
    dem = np.ascontiguousarray(dem, dtype=np.float64)
    valid = np.ascontiguousarray(valid, dtype=np.bool_)
    no_seed = np.ascontiguousarray(no_seed, dtype=np.bool_)
    label, key, parent_cell, _order, n_labels, ez, ea, eb = hydro.priority_flood_labels(dem, valid, no_seed)
    (cap, own_cap, zspill, parent, sibling, link,
     child_a, child_b, n_nodes) = hydro.build_hierarchy(dem.ravel(), label, key, n_labels,
                                                        ez, ea, eb, cell_area)
    return Hierarchy(dem.shape, dem, valid, label, key, parent_cell, n_labels, cap, own_cap,
                     zspill, parent, sibling, link, child_a, child_b, n_nodes, cell_area)


@dataclass
class ScenarioResult:
    lake_depth: np.ndarray     # Wassertiefe in Senken (m)
    flow_volume: np.ndarray    # durchgeflossenes Volumen je Zelle (m³)
    slope: np.ndarray
    runoff_total: float        # m³
    stored: float              # m³ in Senken
    to_ocean: float            # m³ verlässt das Gebiet
    n_full_roots: int


def run_fill_spill(h: Hierarchy, runoff_m: np.ndarray, culverts=None, duration_s=3600.0) -> ScenarioResult:
    """runoff_m: effektiver Abfluss je Zelle in m Wassersäule (nach Verlusten/Kanal).

    culverts: Liste von dicts mit 'einlauf', 'auslauf' (Zellindex) und 'q_m3s'
              -> Durchlässe mit begrenzter Leistung (Volumen = q · Dauer).
    """
    vol = (runoff_m * h.cell_area).ravel()
    vol[~h.valid.ravel()] = 0
    lab = h.label
    runoff_by_label = np.bincount(lab[lab >= 0], weights=vol[lab >= 0], minlength=h.n_labels)
    # Abfluss in Ozean-Gebieten (label 0) fließt direkt hinaus
    direct_out = runoff_by_label[0]
    root = hydro.root_nodes(h.parent, h.n_nodes)
    drain_cap = np.zeros(h.n_nodes)
    drain_tgt = np.full(h.n_nodes, -1, dtype=np.int64)
    for c in culverts or []:
        leaf = lab[c["einlauf"]]
        if leaf <= 0:
            continue
        R = root[leaf]
        drain_cap[R] += c["q_m3s"] * duration_s
        drain_tgt[R] = c["auslauf"]
    water, isfull, over, to_ocean, drained = hydro.fill_spill_drains(
        lab, runoff_by_label, h.own_cap, h.parent, h.sibling, h.link, h.n_nodes,
        root, drain_cap, drain_tgt)
    depth, _ = hydro.lake_levels(h.dem.ravel(), h.key, lab, water, isfull, h.cap, h.own_cap,
                                 h.zspill, h.parent, h.child_a, h.child_b, h.n_nodes,
                                 h.n_labels, h.cell_area)
    depth = depth.reshape(h.shape)

    # Fließwege (MFD): Quellen = Zellabfluss + Überläufe an den Sattelzellen
    source = vol.copy()
    links = h.link[: h.n_nodes]
    m = (over > 0) & (links >= 0)
    np.add.at(source, links[m], over[m])
    md = (drained > 0) & (drain_tgt >= 0)
    np.add.at(source, drain_tgt[md], drained[md])
    absorb = depth > 0.005
    acc, slope = hydro.mfd_accumulate(h.dem, h.valid, source.reshape(h.shape), absorb)

    stored = float(water.sum())
    roots = h.parent < 0
    res = ScenarioResult(depth, acc, slope, float(vol.sum()), stored,
                         float(to_ocean + direct_out), int((isfull & roots).sum()))
    res.durchlass_m3 = float(drained.sum())
    res.source = source
    res.absorb = absorb
    return res


def manning_depth(volume_m3: np.ndarray, slope: np.ndarray, duration_s: float,
                  n_manning: np.ndarray | float, cell_width: float = 1.0,
                  min_slope: float = 0.005) -> np.ndarray:
    """Näherung der Fließtiefe (m) außerhalb der Senken.

    Annahme Blockregen länger als die Fließzeit -> am Regenende stationärer
    Abfluss Q = V / T. Je Zelle (MFD verteilt über die Breite) gilt
    Normalabfluss breites Gerinne:  h = (n·q / √S)^(3/5),  q = Q / Zellbreite.
    """
    q = volume_m3 / duration_s / cell_width
    s = np.maximum(slope, min_slope)
    return np.power(n_manning * q / np.sqrt(s), 0.6)


def breach_corridors(dem, valid, corridor, max_len=1000, min_depth=0.05, iterations=6,
                     max_cut=-1.0, dedupe_m=20.0):
    """Durchsticht Dämme nur innerhalb des Korridors (least-cost breaching, Lindsay 2016).

    Von jedem lokalen Tiefpunkt im Korridor innerhalb einer Senke wird der Weg mit
    dem geringsten Aushub zu tieferem Gelände gesucht (max. max_len Zellen).
      - Aushub <= max_cut (oder max_cut < 0): Weg wird linear abgesenkt (offener Graben).
      - Aushub >  max_cut: Damm bleibt, der Weg wird als Durchlass gemeldet
        (Einlauf = Tiefpunkt, Auslauf = Zielzelle, Dammkrone = Stelle des max. Aushubs).
    Rückgabe: neues DGM, Anzahl abgesenkter Zellen, Liste der Durchlässe (dicts).
    """
    from scipy import ndimage
    z = np.ascontiguousarray(np.where(valid, dem, 0.0), dtype=np.float64)
    valid = np.ascontiguousarray(valid)
    corridor = np.ascontiguousarray(corridor & valid)
    n = z.size
    ncols = z.shape[1]
    cost = np.full(n, np.inf)
    steps = np.zeros(n, dtype=np.int64)
    prev = np.full(n, -1, dtype=np.int64)
    touched = np.empty(n, dtype=np.int64)
    total = 0
    culverts = {}
    for _ in range(iterations):
        filled = hydro.fill_depressions(z, valid)
        dep = (filled - z) > min_depth
        lab, nlab = ndimage.label(dep, structure=np.ones((3, 3)))
        hit = np.unique(lab[dep & corridor])
        hit = hit[hit > 0]
        if len(hit) == 0:
            break
        # Startpunkte: alle lokalen Tiefpunkte im Korridor innerhalb von Senken
        zp = np.pad(z, 1, constant_values=np.inf)
        lower = np.zeros(z.shape, bool)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                lower |= zp[1 + dr:1 + dr + z.shape[0], 1 + dc:1 + dc + z.shape[1]] < z
        starts = np.flatnonzero((corridor & (lab > 0) & ~lower).ravel())
        starts = starts[np.argsort(z.ravel()[starts])[::-1]]   # von oben nach unten
        changed, c_st, c_tg, c_cut, c_cr = hydro.breach_many(
            z, valid, corridor, lab, starts.astype(np.int64), max_len, cost, steps, prev, touched,
            float(max_cut))
        for st, tgt, cut, crest in zip(c_st, c_tg, c_cut, c_cr):
            # gleiche Dammquerung nur einmal erfassen (Raster dedupe_m)
            cr, cc = divmod(int(crest), ncols)
            key = (int(cr // dedupe_m), int(cc // dedupe_m))
            old = culverts.get(key)
            if old is None or cut < old["aushub_m"]:
                culverts[key] = {"einlauf": int(st), "auslauf": int(tgt), "krone": int(crest),
                                 "aushub_m": float(cut)}
        total += changed
        if changed == 0:
            break
    return z, total, list(culverts.values())
