"""Vergleich mit der Hinweiskarte Starkregengefahren (BKG/NLWKN, 2D-hydrodynamisch).

Quelle: WMS https://sgx.geodatenzentrum.de/wms_starkregen (Layer ni_tiefe_agw,
ni_tiefe_extrem). Szenarien laut BKG-Dokumentation: "außergewöhnlich" =
100-jährlich, "extrem" = 100 mm/h; Kanalnetz und Versickerung vernachlässigt.

Der WMS liefert ein eingefärbtes Bild; die Farben werden über die Legende in
Tiefenklassen zurückübersetzt (Klassenuntergrenze in m, 0 = < 10 cm).
"""
from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.colors import BoundaryNorm, ListedColormap
from PIL import Image

WMS = "https://sgx.geodatenzentrum.de/wms_starkregen"
# Legende (RGB) -> Klassen-Untergrenze in m
LEGEND = {
    (204, 236, 255): 0.10,
    (153, 204, 255): 0.30,
    (110, 153, 255): 0.50,
    (61, 102, 255): 1.00,
    (0, 51, 204): 2.00,
    (8, 48, 107): 4.00,
}
CLASSES = [0.0, 0.10, 0.30, 0.50, 1.00, 2.00, 4.00]
CLASS_LABELS = ["<10", "10–30", "30–50", "50–100", "100–200", "200–400", "≥400"]


def fetch_class_raster(bbox, layer="ni_tiefe_extrem", px=1.0, chunk=1000):
    """Lädt die Hinweiskarte für bbox (EPSG:25832) in 1-m-Auflösung als Klassenraster."""
    xmin, ymin, xmax, ymax = bbox
    W, H = int(round((xmax - xmin) / px)), int(round((ymax - ymin) / px))
    out = np.zeros((H, W), np.float32)
    unknown = 0
    for r0 in range(0, H, chunk):
        for c0 in range(0, W, chunk):
            h, w = min(chunk, H - r0), min(chunk, W - c0)
            bx = (xmin + c0 * px, ymax - (r0 + h) * px, xmin + (c0 + w) * px, ymax - r0 * px)
            params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer,
                      "STYLES": "", "CRS": "EPSG:25832", "BBOX": ",".join(f"{v:.3f}" for v in bx),
                      "WIDTH": w, "HEIGHT": h, "FORMAT": "image/png", "TRANSPARENT": "TRUE"}
            r = requests.get(WMS, params=params, timeout=120)
            r.raise_for_status()
            a = np.array(Image.open(io.BytesIO(r.content)).convert("RGBA"))
            m = a[..., 3] > 0
            cls = np.zeros((h, w), np.float32)
            known = np.zeros((h, w), bool)
            for rgb, v in LEGEND.items():
                hit = m & (a[..., 0] == rgb[0]) & (a[..., 1] == rgb[1]) & (a[..., 2] == rgb[2])
                cls[hit] = v
                known |= hit
            unknown += int((m & ~known).sum())
            out[r0:r0 + h, c0:c0 + w] = cls
    return out, unknown


def to_class(depth):
    """Eigene Tiefe (m) -> gleiche Klassen-Untergrenzen wie die Hinweiskarte."""
    out = np.zeros_like(depth, dtype=np.float32)
    for v in CLASSES[1:]:
        out[depth >= v] = v
    return out


def compare(own_depth, ref_cls, mask=None, thresholds=(0.10, 0.30, 0.50)):
    """Kennzahlen je Schwelle: Trefferquote (POD), Fehlalarmquote (FAR), CSI."""
    if mask is None:
        mask = np.ones_like(own_depth, bool)
    own_cls = to_class(own_depth)
    res = {}
    for t in thresholds:
        a = (own_cls >= t) & mask
        b = (ref_cls >= t) & mask
        hit = int((a & b).sum())
        miss = int((~a & b).sum())
        fa = int((a & ~b).sum())
        res[f"{int(t * 100)}cm"] = {
            "flaeche_eigen_m2": int(a.sum()), "flaeche_hwk_m2": int(b.sum()),
            "trefferquote": hit / max(1, hit + miss),
            "fehlalarmquote": fa / max(1, hit + fa),
            "csi": hit / max(1, hit + miss + fa),
        }
    # Klassenübereinstimmung (nur wo eine der beiden Karten >= 10 cm zeigt)
    wet = ((own_cls > 0) | (ref_cls > 0)) & mask
    res["klassen_gleich_anteil"] = float((own_cls[wet] == ref_cls[wet]).mean()) if wet.any() else float("nan")
    res["klassen_max_1_abweichung"] = float(
        (np.abs(np.searchsorted(CLASSES, own_cls[wet]) - np.searchsorted(CLASSES, ref_cls[wet])) <= 1).mean()
    ) if wet.any() else float("nan")
    return res


def plot(path, hill, own_depth, ref_cls, title, building=None):
    cmap = ListedColormap(["#ccecff", "#99ccff", "#6e99ff", "#3d66ff", "#0033cc", "#08306b"])
    norm = BoundaryNorm(CLASSES[1:] + [100], cmap.N)
    own_cls = to_class(own_depth)
    fig, axs = plt.subplots(1, 3, figsize=(19, 6.8), constrained_layout=True)
    for ax, data, t in ((axs[0], own_cls, "Starkregen-Spiegel"), (axs[1], ref_cls, "Hinweiskarte BKG/NLWKN (2D)")):
        ax.imshow(hill, cmap="gray", vmin=0, vmax=1)
        if building is not None:
            ax.imshow(np.ma.masked_where(~building, np.ones_like(hill)), cmap="autumn", alpha=0.35)
        im = ax.imshow(np.ma.masked_less(data, 0.10), cmap=cmap, norm=norm)
        ax.set_title(t)
        ax.set_xticks([])
        ax.set_yticks([])
    cb = fig.colorbar(im, ax=axs[:2], shrink=0.7, ticks=CLASSES[1:])
    cb.ax.set_yticklabels([f"≥{int(v * 100)} cm" for v in CLASSES[1:]])
    # Differenzbild: wo nass in welcher Karte (>= 10 cm)
    a, b = own_cls >= 0.10, ref_cls >= 0.10
    diff = np.zeros(hill.shape + (4,))
    diff[a & b] = (0.1, 0.5, 0.9, 0.9)       # beide
    diff[a & ~b] = (0.95, 0.55, 0.1, 0.9)    # nur Starkregen-Spiegel
    diff[~a & b] = (0.6, 0.2, 0.7, 0.9)      # nur Hinweiskarte
    axs[2].imshow(hill, cmap="gray", vmin=0, vmax=1)
    axs[2].imshow(diff)
    axs[2].set_title("Vergleich ≥ 10 cm: blau = beide, orange = nur wir, violett = nur Hinweiskarte")
    axs[2].set_xticks([])
    axs[2].set_yticks([])
    fig.suptitle(title, fontsize=14)
    fig.savefig(path, dpi=100)
    plt.close(fig)
