"""Download der Open-Data-Grundlagen des LGLN über die STAC-Kataloge.

DGM1:  https://dgm.stac.lgln.niedersachsen.de   (Kacheln 1 × 1 km, COG)
ALKIS: https://alkis.stac.lgln.niedersachsen.de (Landkreis-Geopackage mit Gebäuden,
       Flurstücken und tatsächlicher Nutzung)
Die Dateien selbst liegen auf *.s3.eu-de.cloud-object-storage.appdomain.cloud.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import requests
from pyproj import Transformer

DGM_STAC = "https://dgm.stac.lgln.niedersachsen.de/search"
ALKIS_STAC = "https://alkis.stac.lgln.niedersachsen.de/search"
UA = {"User-Agent": "starkregen-spiegel/0.1 (Pilotprojekt Osterholz-Scharmbeck)"}


def _bbox_wgs(bbox, crs="EPSG:25832"):
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    xs, ys = zip(*[t.transform(x, y) for x, y in
                   [(bbox[0], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[1]), (bbox[2], bbox[3])]])
    return [min(xs), min(ys), max(xs), max(ys)]


def _download(url, path: Path, log=print):
    if path.exists() and path.stat().st_size > 0:
        log(f"  vorhanden: {path.name}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with requests.get(url, stream=True, timeout=600, headers=UA) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(path)
    log(f"  geladen: {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def dgm_tiles(bbox, folder: Path, log=print):
    """Lädt je 1-km-Kachel den neuesten DGM1-Stand, der das Gebiet überlappt."""
    # STAC-Suche mit leicht verkleinerter Box, damit nur echte Nachbarn kommen
    b = [bbox[0] + 1, bbox[1] + 1, bbox[2] - 1, bbox[3] - 1]
    r = requests.get(DGM_STAC, params={"collections": "dgm1", "bbox": ",".join(map(str, _bbox_wgs(b))),
                                       "limit": 500}, timeout=60, headers=UA)
    r.raise_for_status()
    newest = {}
    for f in r.json()["features"]:
        parts = f["id"].split("_")            # dgm1_32_486_5897_1_ni_2025
        e, n, year = int(parts[2]), int(parts[3]), int(parts[-1])
        if not (bbox[0] // 1000 <= e < -(-bbox[2] // 1000) and bbox[1] // 1000 <= n < -(-bbox[3] // 1000)):
            continue
        if (e, n) not in newest or year > newest[(e, n)][0]:
            newest[(e, n)] = (year, f["assets"]["dgm1-tif"]["href"])
    log(f"DGM1: {len(newest)} Kacheln")
    return [_download(url, Path(folder) / Path(url).name, log) for _, url in sorted(newest.values())]


def alkis_landkreis(folder: Path, name_part="Osterholz", log=print):
    """Lädt das ALKIS-Geopackage des Landkreises und entpackt es."""
    r = requests.get(ALKIS_STAC, params={"collections": "alkis-landkreise", "limit": 100},
                     timeout=60, headers=UA)
    r.raise_for_status()
    for f in r.json()["features"]:
        if name_part.lower() in f["id"].lower():
            url = f["assets"]["alkis-landkreis-gpkg-zip"]["href"]
            z = _download(url, Path(folder) / Path(url).name, log)
            with zipfile.ZipFile(z) as zf:
                names = [n for n in zf.namelist() if n.endswith(".gpkg")]
                zf.extractall(folder, members=names)
            return [Path(folder) / n for n in names]
    raise RuntimeError(f"Kein ALKIS-Landkreis mit '{name_part}' im STAC gefunden.")
