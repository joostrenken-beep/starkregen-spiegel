"""KOSTRA-DWD-2020: Niederschlagshöhen je Dauerstufe und Wiederkehrzeit (Open Data DWD)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import Point

BASE = ("https://opendata.dwd.de/climate_environment/CDC/grids_germany/return_periods/"
        "precipitation/KOSTRA/KOSTRA_DWD_2020/gis/")
T_LIST = [1, 2, 3, 5, 10, 20, 30, 50, 100]


def _shp(folder: Path, dauer_min: int) -> Path:
    d = f"{dauer_min:05d}"
    shp = folder / f"D{d}" / f"GIS_KOSTRA-DWD-2020_D{d}" / f"KOSTRA-DWD-2020_D{d}.shp"
    if not shp.exists():
        folder.mkdir(parents=True, exist_ok=True)
        z = folder / f"GIS_D{d}.zip"
        r = requests.get(f"{BASE}GIS_KOSTRA-DWD-2020_D{d}.zip", timeout=300)
        r.raise_for_status()
        z.write_bytes(r.content)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(folder / f"D{d}")
    return shp


def werte(folder: Path, dauer_min: int, x: float, y: float, crs="EPSG:25832") -> dict:
    """Niederschlagshöhen hN (mm) je Wiederkehrzeit am Punkt (x, y)."""
    k = gpd.read_file(_shp(Path(folder), dauer_min))
    p = gpd.GeoSeries([Point(x, y)], crs=crs).to_crs(k.crs).iloc[0]
    row = k[k.contains(p)]
    if row.empty:
        row = k.iloc[[k.distance(p).argmin()]]
    row = row.iloc[0]
    return {t: float(row[f"HN_{t:03d}A_"]) for t in T_LIST}


def jaehrlichkeit_text(mm: float, hn: dict) -> str:
    """Einordnung einer Regenmenge (gleiche Dauer) in die KOSTRA-Wiederkehrzeiten."""
    if mm > hn[100]:
        return "seltener als 100-jährlich" if mm < 1.6 * hn[100] else "extrem (weit über 100-jährlich)"
    for t in T_LIST:
        if mm <= hn[t] * 1.03:
            return f"ca. {t}-jährlich"
    return "seltener als 100-jährlich"
