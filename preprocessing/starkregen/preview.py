"""Kontrollbilder (PNG) für die fachliche Prüfung."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm


def hillshade(dem, az=315, alt=45, cell=1.0):
    d = np.where(np.isnan(dem), np.nanmean(dem), dem)
    gy, gx = np.gradient(d, cell)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(360 - az + 90), np.radians(alt)
    hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)


DEPTH_CMAP = LinearSegmentedColormap.from_list(
    "tiefe", ["#c6e6ff", "#6fb6f2", "#2b7bd6", "#1446a0", "#0b1f5c"])


def _base(ax, dem, extent, building):
    ax.imshow(hillshade(dem), cmap="gray", extent=extent, vmin=0, vmax=1)
    if building is not None:
        b = np.ma.masked_where(~building, np.ones_like(dem))
        ax.imshow(b, cmap=LinearSegmentedColormap.from_list("b", ["#d2691e", "#d2691e"]),
                  extent=extent, alpha=0.55)
    ax.set_xticks([])
    ax.set_yticks([])


def save_panel(path, dem, extent, building, depth, flow, sinks, title):
    fig, axs = plt.subplots(1, 3, figsize=(18, 6.6), constrained_layout=True)
    # Wassertiefe
    _base(axs[0], dem, extent, building)
    dm = np.ma.masked_less(depth, 0.02)
    im = axs[0].imshow(dm, cmap=DEPTH_CMAP, vmin=0, vmax=1.0, extent=extent)
    fig.colorbar(im, ax=axs[0], shrink=0.7, label="max. Wassertiefe [m]")
    axs[0].set_title("Wassertiefe (Senken + Fließtiefe)")
    # Fließwege
    _base(axs[1], dem, extent, building)
    fm = np.ma.masked_less(flow, 1.0)
    im = axs[1].imshow(fm, cmap="viridis_r", norm=LogNorm(1, max(10, np.nanmax(flow))), extent=extent)
    fig.colorbar(im, ax=axs[1], shrink=0.7, label="durchgeflossenes Volumen [m³]")
    axs[1].set_title("Fließwege (MFD)")
    # Senken
    _base(axs[2], dem, extent, building)
    sm = np.ma.masked_less(sinks, 0.05)
    im = axs[2].imshow(sm, cmap="magma_r", vmin=0, vmax=max(0.5, float(np.nanpercentile(sinks[sinks > 0.05], 99)) if (sinks > 0.05).any() else 1), extent=extent)
    fig.colorbar(im, ax=axs[2], shrink=0.7, label="Senkentiefe bei Vollfüllung [m]")
    axs[2].set_title("Senken (Fassungsvermögen)")
    fig.suptitle(title, fontsize=14)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def save_single(path, dem, extent, building, layer, kind, title):
    fig, ax = plt.subplots(figsize=(10, 10), constrained_layout=True)
    _base(ax, dem, extent, building)
    if kind == "depth":
        im = ax.imshow(np.ma.masked_less(layer, 0.02), cmap=DEPTH_CMAP, vmin=0, vmax=1.0, extent=extent)
        fig.colorbar(im, ax=ax, shrink=0.6, label="max. Wassertiefe [m]")
    ax.set_title(title)
    fig.savefig(path, dpi=130)
    plt.close(fig)
