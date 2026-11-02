# Starkregen-Spiegel

Web-App (PWA, mobil zuerst): Bürgerinnen und Bürger geben ihre Adresse ein und
sehen, wie Oberflächenwasser bei Starkregen über und um ihr Grundstück fließt, wo
es sich sammelt und wie hoch es an der Hauswand stehen könnte.

> **Orientierungshilfe – keine amtliche Starkregengefahrenkarte, keine Haftung.**

Pilotgebiet: Osterholz-Scharmbeck, Innenstadt (2 × 2 km, gerechnet mit 1 km Puffer).

## Stand

| Meilenstein | Inhalt | Status |
|---|---|---|
| M0 | Gerüst, Konfiguration, Python-Umgebung | ✅ |
| M1 | Vorverarbeitung (Senken, Füll-und-Überlauf, Fließwege, Kontrollbilder) | ✅ Pilotgebiet gerechnet (ALKIS + DGM1 2025), Vergleich mit Hinweiskarte |
| M2 | Karte mit Adresssuche und Layern (PWA) | ✅ |
| M3 | Steckbrief, Szenarien, 3D | offen |

## Ordnerstruktur

```
config/pilot.yaml        alle Parameter (Gebiet, Abflussbildung, Szenarien, Ampel)
data/raw/dgm1/           DGM1-Kacheln (GeoTIFF, nicht im Repo)
data/raw/osm/            OSM-Export (nicht im Repo)
data/processed/          Ergebnisse der Vorverarbeitung (nicht im Repo)
preprocessing/
  run_pipeline.py        Hauptskript
  starkregen/hydro.py    Rechenkern (Senkenhierarchie, Füll-und-Überlauf, MFD)
  starkregen/model.py    Python-Schnittstelle, Manning-Fließtiefe
  starkregen/landcover.py  Gebäude, Versiegelung, Durchlässe, Abflussbildung
  starkregen/osm.py      Overpass-Abruf und Einlesen
  starkregen/dgm.py      Kacheln zusammensetzen
  tests/                 Funktionstests mit künstlichem Gelände
docs/                    Methodik, Overpass-Abfrage
web/                     Web-App (Vite + MapLibre, PWA); Daten in web/public/data
```

## Schritt für Schritt: Vorverarbeitung

### 1. Python-Umgebung (einmalig)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Daten besorgen

**DGM1 (LGLN OpenGeoData.NI):** 1-m-Raster, Cloud Optimized GeoTIFF, ETRS89/UTM32
(EPSG:25832), Kacheln 1 × 1 km, Stand 2025. Automatisch mit `--fetch` (über den
STAC-Katalog `dgm.stac.lgln.niedersachsen.de`) oder manuell, z. B.
`dgm1_32_486_5897_1_ni_2025.tif`, unverändert nach `data/raw/dgm1/`.

- Test (M1): Kachel **E 486 / N 5897**
- Pilot: 16 Kacheln, **E 484–487 × N 5895–5898**

**OSM:** automatisch mit `--fetch-osm` oder manuell: Inhalt von
`docs/overpass_abfrage.txt` auf <https://overpass-turbo.eu> ausführen,
„Export → Rohdaten (JSON)“ und als `data/raw/osm/osm_rechengebiet.json` speichern.

### 3. Rechnen

```bash
python -m pytest preprocessing/tests -q                       # Funktionstest (≈10 s)
python preprocessing/run_pipeline.py --test --fetch      # eine Kachel (lädt DGM1 + ALKIS)
python preprocessing/run_pipeline.py                           # ganzes Gebiet
```

Ergebnisse unter `data/processed/test/` bzw. `data/processed/pilot/`:

| Datei | Inhalt |
|---|---|
| `pXX_kontrolle.png` | Kontrollbild: Wassertiefe, Fließwege, Senken |
| `pXX_wassertiefe.tif` | max. Wassertiefe (m) = max(Senkenfüllung, Fließtiefe) |
| `pXX_fliessvolumen.tif` | durchgeflossenes Volumen je Zelle (m³) |
| `senken_tiefe.tif` | Senkentiefe bei Vollfüllung (m) |
| `gebaeude_ergebnis.gpkg` | je Gebäude max. Tiefe im 2-m-Ring (für den Steckbrief) |
| `statistik.json` | Wasserbilanz je Szenario (muss aufgehen!) |
| `risiko_objektliste.csv` | S3/S4-Gebäude im Kerngebiet mit Schutzwürdigkeit, Begründung, Gefährdung/Risiko je Szenario, Schutzziel erreicht ja/nein (Excel: Trennzeichen „;“) |
| `plausibilitaet.md` | Plausibilitätsbericht: Wo liegen die Senken (Gewässer, Unterführung, Straße, Garten …)? Folgen die Fließwege Gräben/Straßen? Die 15 größten Senken mit Links zu Luftbild und OSM |
| `plausi/senke_NN.png` | Bildausschnitte der größten Senken |

## Plausibilität ohne Ortskenntnis

1. **Plausibilitätsbericht** (automatisch, s. o.) – Senken werden anhand von OSM
   eingeordnet; „damm“ = Verdacht auf fehlenden Durchlass.
2. **Vergleich mit der Hinweiskarte Starkregengefahren** (BKG/NLWKN,
   hydrodynamisches 2D-Modell, WMS `https://sgx.geodatenzentrum.de/wms_starkregen`).
   Die Pipeline rechnet dafür einen Vergleichslauf mit denselben Annahmen wie die
   Hinweiskarte (100 mm in 1 h, ohne Kanal und Versickerung) und gibt aus:
   `hwk_vergleich.png` (nebeneinander + Differenzbild) und Kennzahlen in
   `statistik.json` (Trefferquote, Fehlalarmquote, CSI je Tiefenschwelle).

## Web-App (Meilenstein 2)

Die Pipeline schreibt am Ende alle Daten für die App nach `web/public/data/`
(Wassertiefe als Bild je Szenario, Fließwege, Senken, Gebäude mit Ampel,
Adressindex). Danach:

```bash
cd web
npm install          # einmalig
npm run dev          # Entwicklungsserver: http://localhost:5173
npm run build        # fertige statische Seite in web/dist/
```

**Veröffentlichen (GitHub Pages):** einmalig im Repo *Settings → Pages → Source:
GitHub Actions* einstellen. Danach baut und veröffentlicht der Workflow
`.github/workflows/pages.yml` die App bei jedem Push auf `main` automatisch unter
`https://<benutzer>.github.io/starkregen-spiegel/`.

**Auf dem iPhone:** Seite in Safari öffnen → Teilen → „Zum Home-Bildschirm“.
Die App startet dann wie eine normale App und funktioniert nach dem ersten
Laden auch ohne Netz (Hintergrundkarte nur für bereits angesehene Bereiche).

Funktionen:
- Adresssuche über eigenen ALKIS-Adressindex (offline, ohne Rate-Limit);
  Nominatim nur als Rückfallebene (auf Enter, max. 1 Anfrage/s)
- Ebenen: Wassertiefe, animierte Fließwege (Richtung = Bewegung,
  Breite = Wassermenge), Senken, Gebäude-Ampel
- Regler für die Szenarien 30/50/80 mm in 1 h
- Antippen eines Gebäudes: Ampel, max. Wassertiefe am Haus je Szenario,
  Hinweis „kleine Mulde am Haus“
- Hinweis „keine amtliche Karte“ beim ersten Start und jederzeit über den Link
- Umschalter **Gefährdung | Risiko**: Risiko = Gefährdung am Gebäude × Schutzwürdigkeit
  (S1–S4 aus ALKIS, angelehnt an DWA-A 118 / M 119); besonders schutzwürdige
  Gebäude violett umrandet; Info zeigt Schutzwürdigkeit, Begründung und Schutzziel

## Methodik (Kurzfassung)

Siehe [docs/methodik.md](docs/methodik.md).
