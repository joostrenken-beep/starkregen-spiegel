# Starkregen-Spiegel

Web-App (PWA, mobil zuerst): Bürgerinnen und Bürger geben ihre Adresse ein und
sehen, wie Oberflächenwasser bei Starkregen über und um ihr Grundstück fließt, wo
es sich sammelt und wie hoch es an der Hauswand stehen könnte.

> **Orientierungshilfe – keine amtliche Starkregengefahrenkarte, keine Haftung.**

Gebiet: **Stadt Osterholz-Scharmbeck, 147 km²** (alle 11 Gemarkungen), gerechnet in
20 Blöcken à 4 × 4 km mit je 1 km Puffer. Der Pilot (Innenstadt 2 × 2 km) bleibt als
schnelles Testgebiet erhalten (`config/pilot.yaml`).

## Stand

| Meilenstein | Inhalt | Status |
|---|---|---|
| M0 | Gerüst, Konfiguration, Python-Umgebung | ✅ |
| M1 | Vorverarbeitung (Senken, Füll-und-Überlauf, Fließwege, Kontrollbilder) | ✅ Pilotgebiet gerechnet (ALKIS + DGM1 2025), Vergleich mit Hinweiskarte |
| M2 | Karte mit Adresssuche und Layern (PWA) | ✅ |
| M3 | Grundstücks-Steckbrief, Einzugsgebiet je Gebäude, Selbstschutz-Tipps, 3D-Ansicht | ✅ |
| M4 | KOSTRA-Kanalabschlag, Versickerung nach Boden, Hinweiskarte als Vergleich, Durchlass-Prüfliste | ✅ |
| M5 | PDF-Export (Steckbrief, Kartenausschnitt), rote Markierung S4 ≥ 30 cm bei T ≈ 30 a, einklappbares Bedienfeld, Datenschutzhinweis (Impressum folgt bei öffentlicher Verbreitung) | ✅ |

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

### 3. Rechnen – ganzes Stadtgebiet (Web-App)

```bash
python preprocessing/run_stadt.py --config config/osterholz-scharmbeck.yaml --fetch
```

- lädt DGM1-Kacheln und ALKIS, erzeugt den Stadtumriss aus ALKIS (Gemeinde 03356007)
- rechnet Block für Block; fertige Blöcke liegen in `data/processed/stadt/bloecke/`.
  **Abbruch? Einfach denselben Befehl erneut starten** – fertige Blöcke werden übersprungen.
- schreibt die Web-Daten nach `web/public/data/` (Wassertiefe als PMTiles-Kacheln je
  Szenario, 1-km-Pakete in `zellen/`, Adressen mit Ortsteil, Stadtumriss)
- Gesamtergebnisse: `data/processed/stadt/gebaeude_ergebnis.gpkg`,
  `risiko_objektliste.csv` (alle S3/S4-Gebäude der Stadt), `statistik.json`
  (inkl. Vergleich mit der Hinweiskarte für das ganze Stadtgebiet)
- nur den Web-Export neu erzeugen: `--nur-export`

Danach den Steckbrief ergänzen (Flurstück, Wasser auf dem Grundstück, Einzugsgebiete,
Gebäudehöhen aus DOM1, Geländekacheln für 3D):

```bash
python preprocessing/run_steckbrief.py --config config/osterholz-scharmbeck.yaml
```

- lädt die DOM1-Kacheln selbst (`data/raw/dom1/`), fertige Blöcke (`m3.pkl`) werden übersprungen
- **nach jedem neuen `run_stadt.py`-Export erneut aufrufen** (mit `--nur-export` genügt,
  solange sich die Blockberechnung nicht geändert hat; sonst vorher die `m3.pkl` löschen)
- Veröffentlichen: `bash preprocessing/daten_veroeffentlichen.sh`, dann Workflow
  „Web-App veröffentlichen“ starten

### 3a. Rechnen – Pilot (schneller Test)

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
- Drei Ansichten für die Gebäude:
  **Wasser am Haus** (unter 10 / 10–30 / 30–50 / über 50 cm) |
  **Schadenspotenzial** S1–S4 (Gebäudeart aus ALKIS, angelehnt an DWA-A 118 / M 119,
  unabhängig vom Regen) | **Risiko** (Wasser am Haus × Schadenspotenzial)
- Filter S1–S4: jede Ansicht lässt sich auf einzelne Schadenspotenzial-Klassen
  beschränken (z. B. Wasser am Haus nur für Schulen, Kitas, Pflege = S4)
- Info zeigt Schadenspotenzial mit Begründung und Schutzziel

## Methodik (Kurzfassung)

Siehe [docs/methodik.md](docs/methodik.md).
