"""DGM1-Kacheln: benötigte Kacheln bestimmen, einlesen und zusammensetzen."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.windows import from_bounds


def needed_tiles(bbox, size=1000):
    """Liste der 1-km-Kacheln (linke untere Ecke in km), die das Gebiet abdecken."""
    xmin, ymin, xmax, ymax = bbox
    return [(e, n) for n in range(int(ymin // size), int(np.ceil(ymax / size)))
            for e in range(int(xmin // size), int(np.ceil(xmax / size)))]


def find_tile_files(folder: Path):
    folder = Path(folder)
    files = sorted([*folder.glob("*.tif"), *folder.glob("*.tiff"), *folder.glob("*.TIF")])
    return files


def mosaic(folder: Path, bbox, nodata_out=np.nan):
    """Setzt alle Kacheln im Ordner zusammen und schneidet auf bbox zu.

    Rückgabe: dem (float64, NaN = keine Daten), transform, crs
    """
    files = find_tile_files(folder)
    if not files:
        raise FileNotFoundError(f"Keine DGM-Kacheln (*.tif) in {folder} gefunden.")
    srcs = []
    try:
        for f in files:
            s = rasterio.open(f)
            b = s.bounds
            if b.right <= bbox[0] or b.left >= bbox[2] or b.top <= bbox[1] or b.bottom >= bbox[3]:
                s.close()
                continue
            srcs.append(s)
        if not srcs:
            raise FileNotFoundError("Keine Kachel überlappt das Rechengebiet "
                                    f"{bbox}. Koordinatensystem EPSG:25832?")
        crs = srcs[0].crs
        arr, transform = merge(srcs, bounds=bbox, nodata=-9999.0, dtype="float64")
        dem = arr[0].astype(np.float64)
        nd = srcs[0].nodata
        dem[dem == -9999.0] = np.nan
        if nd is not None:
            dem[dem == nd] = np.nan
        dem[dem < -100] = np.nan
    finally:
        for s in srcs:
            s.close()
    return dem, transform, crs


def missing_tiles(folder: Path, bbox):
    have = set()
    for f in find_tile_files(folder):
        with rasterio.open(f) as s:
            have.add((int(round(s.bounds.left / 1000)), int(round(s.bounds.bottom / 1000))))
    return [t for t in needed_tiles(bbox) if t not in have]
