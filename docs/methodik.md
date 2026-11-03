# Methodik Starkregen-Spiegel (Stand M3)

Vereinfachte topografische Gefährdungsanalyse (vgl. DWA-M 119), ergänzt um eine
Näherung der Fließtiefe auf geneigten Flächen. **Kein hydrodynamisches 2D-Modell.**

## 1. Geländeaufbereitung
- DGM1 (LGLN, Stand 2025), 1 m, 16 Kacheln (Kern 2 × 2 km + 1 km Puffer).
- **Amtliche Grundlagen aus ALKIS** (LGLN Open Data, Landkreis-Geopackage):
  Gebäude, tatsächliche Nutzung, Bauwerke (Durchlässe, Brücken), Flurstücke,
  Adressen. OSM bleibt als Alternative (`gebaeude.quelle: osm`).
- **Gebäude einbrennen:** + 5 m → Wasser fließt um Häuser herum. Aufgeständerte
  und unterirdische Gebäude werden nicht eingebrannt.
- **Innenhöfe:** Wasser, das nur wegen umschließender Gebäude stehen würde, fließt
  ab 0,30 m über die angrenzenden Gebäude ab (Tor, Durchgang, Türschwelle).
- **Durchlässe / Dämme (least-cost breaching, Lindsay 2016):** Dämme werden nur
  innerhalb eines Korridors durchstochen: ALKIS-Fließgewässer (+2 m),
  ALKIS-Durchlässe (+25 m) und vermutete Durchlässe (Gewässer an Straße/Bahn
  unterbrochen und innerhalb 40 m fortgesetzt). Von jedem Tiefpunkt im Korridor
  wird der Weg mit dem geringsten Aushub zu tieferem Gelände gesucht.
  - Aushub ≤ 0,5 m → offener Graben (DGM wird abgesenkt).
  - Aushub > 0,5 m (Damm) → **Durchlass mit begrenzter Leistung**: Der Damm bleibt;
    aus dem Senkensystem fließen höchstens Q·T zum Auslauf, der Rest staut sich
    und läuft ggf. über die Dammkrone. Standard Q = 1,0 m³/s (≈ DN 800–1000 voll),
    bewusst vorsichtig. Bekannte Werte: `durchlaesse.einzelwerte` in der Konfiguration.
  Ausgaben: `durchstich_m.tif`, `durchlaesse.geojson`, `durchlaesse_vermutet.geojson`.

## 2. Abflussbildung (Blockregen P in T)
Je Zelle Versiegelungsgrad *s* (0…1) aus der ALKIS-Nutzung (Tabelle in
`config/pilot.yaml`, z. B. Straßenverkehr 0,9, Wohnbaufläche offen 0,3,
geschlossen 0,6, Gewerbe 0,75, Bahn 0,15, Landwirtschaft/Wald 0). Gewässer:
Regen fließt vollständig ab.

```
Abfluss = s · max(0, P − Benetzung − Kanalabschlag·T)
        + (1 − s) · max(0, P − Anfangsverlust − Infiltration·T)
```

| Parameter | Standard | Begründung |
|---|---|---|
| Benetzungsverlust versiegelt | 1,5 mm | übliche Größenordnung |
| Kanalabschlag (nur versiegelt/Dach) | 20 mm/h | pauschal ≈ 2–3-jährlicher Stundenregen; mit KOSTRA-DWD-2020 prüfen |
| Anfangsverlust unversiegelt | 5 mm | Interzeption, Mulden |
| Infiltration unversiegelt | 10 mm/h | eher vorsichtig; Geest-Sand höher, Niederung/Moor niedriger |

**Dächer:** wie versiegelt; der Überschuss über die Kanalkapazität tritt
gleichmäßig im 2-m-Ring um das Gebäude aufs Gelände (überlastete Fallrohre).

## 3. Senkenhierarchie und Füll-und-Überlauf
Priority-Flood von allen Tiefpunkten und vom Gebietsrand → Teileinzugsgebiete
und Sattelhöhen. Daraus ein Senkenbaum (Kruskal): Senken laufen über den
niedrigsten Sattel in die Nachbarsenke über; sind beide voll, steigt der
gemeinsame Spiegel. Das Fassungsvermögen wird exakt aus dem DGM integriert.
Literatur: Barnes, Callaghan & Wickert (2020/21), *Earth Surf. Dynam.*

Kontrolle: Oberflächenabfluss = in Senken + aus dem Gebiet (Fehler ≈ 1e-8 m³).

## 4. Fließwege und Fließtiefe
- Fließwege: Mehrfach-Fließrichtungen (MFD, Quinn/Freeman, Exponent 1,1) auf dem
  eingebrannten DGM. Gefüllte Senken nehmen Wasser auf; Überläufe werden am
  Sattel wieder eingespeist.
- Fließtiefe außerhalb von Senken: stationärer Abfluss am Regenende
  Q = V/T, Normalabfluss breites Gerinne h = (n·q/√S)^(3/5),
  n = 0,015 (versiegelt) bzw. 0,05 (unversiegelt), S ≥ 0,5 %.
- Max. Wassertiefe = max(Senkenfüllung, Fließtiefe).

## 5. Gebäude-Ampel
Ring 2 m um das Gebäude. Rot, wenn ≥ 30 cm, gelb, wenn ≥ 10 cm, jeweils auf
**mindestens 5 m²** des Rings. Kleinere tiefe Stellen (Kellerabgang, Lichtschacht,
DGM-Artefakt an der Wand) → eigener Hinweis „kleine Mulde am Haus“.

## 6. Prüfung gegen die Hinweiskarte Starkregengefahren (BKG/NLWKN)
Vergleichslauf mit deren Annahmen (100 mm/h, ohne Kanal/Versickerung), Stand
erster Pilotlauf (Kerngebiet 2 × 2 km):
- Senken, Unterführungen, Teiche und Niederungen stimmen in Lage und Größe überein.
- Fehlalarme selten (≈ 10 % der als nass gezeigten Fläche).
- Wir zeigen weniger flächige Überströmung (10–50 cm) entlang von Straßen und
  Gewässern → bekannte Schwäche der statischen Methode.
- Bachtal Südwest (≈ E 485 600 / N 5 896 400): Die Hinweiskarte staut den Bach
  vor Straßen-/Bahndamm bis 4 m auf (Damm im DGM geschlossen). Mit unbegrenzten
  Durchlässen wichen ≈ 460 Gebäude ab; mit begrenzter Leistung (1 m³/s) staut es
  auch bei uns. Entscheidend ist die tatsächliche Leistungsfähigkeit der Durchlässe.
- Stand mit begrenzten Durchlässen: Fläche ≥ 10 cm Treffer 45 %, Fehlalarm 7 %;
  ≥ 30 cm Treffer 54 %; **Gebäude-Ampel gleich bei 72 %** von 5 286 Gebäuden.

## 7. Risikobetrachtung: Gefährdung × Schutzwürdigkeit
Zweite Betrachtungsweise neben der reinen Gefährdung („Risiko“-Ansicht in der App).

**Schutzwürdigkeit S1–S4** – nur aus öffentlichen ALKIS-Daten (Gebäudefunktion,
Lage zur Erdoberfläche, Bauart, Grundfläche, tatsächliche Nutzung des Flurstücks),
in Anlehnung an DWA-A 118 (2006), Tab. 3 *Empfohlene Überflutungshäufigkeiten*,
und DWA-M 119 (Schadenspotenzial):

| Klasse | Gebäude (Beispiele) | Bezug |
|---|---|---|
| S1 gering | Garagen, Schuppen, Nebengebäude, offene Hallen, Landwirtschaft | A 118: ländliche Gebiete 1 in 10 a |
| S2 mäßig | Wohngebäude (Keller unbekannt) | A 118: Wohngebiete 1 in 20 a |
| S3 hoch | Gewerbe, Handel, Wohn-/Geschäftshäuser, Verwaltung, Versammlung, Kirchen, Versorgung | A 118: Stadtzentren, Gewerbe 1 in 30 a |
| S4 sehr hoch | Schulen, Kitas, Krankenhaus/Pflege (auch über Flurstücksnutzung Gesundheit/Soziales/Bildung/Sicherheit), Feuerwehr, Polizei, Tiefgaragen/unterirdische Gebäude, KRITIS | A 118: unterirdische Anlagen 1 in 50 a; M 119: sehr hohes Schadenspotenzial |

Nebengebäude: ALKIS „Gebäude für Wirtschaft oder Gewerbe“ < 40 m² bzw. < 150 m² auf
Wohnbaufläche. **Keller sind in öffentlichen Daten nicht enthalten** – eine
Unterscheidung „Wohnen ohne Keller“ braucht Ortsdaten (z. B. Bauakte, Begehung).
Denkmalschutz ist nicht als offener Datensatz verfügbar.

**Gefährdung am Gebäude** (Tiefe auf ≥ 5 m² im 2-m-Ring): 0 < 10 cm, 1 ≥ 10 cm,
2 ≥ 30 cm, 3 ≥ 50 cm.

**Risikomatrix** (vgl. M 119), Ergebnis gering / mäßig / hoch / sehr hoch:

| Gefährdung \ Schutzwürdigkeit | S1 | S2 | S3 | S4 |
|---|---|---|---|---|
| < 10 cm | gering | gering | gering | mäßig |
| 10–30 cm | gering | mäßig | mäßig | hoch |
| 30–50 cm | mäßig | mäßig | hoch | sehr hoch |
| ≥ 50 cm | mäßig | hoch | sehr hoch | sehr hoch |

**Schutzziel (A 118-orientiert):** je Klasse ein Szenario, bis zu dem das Gebäude
trocken (< 10 cm) bleiben soll: S1/S2 → 30 mm, S3 → 50 mm, S4 → 80 mm in 1 h.
Die Zuordnung Szenario ↔ Wiederkehrzeit ist eine Annahme und mit KOSTRA-DWD-2020
festzulegen. Ausgabe `risiko_objektliste.csv`: alle S3/S4-Gebäude im Kerngebiet,
sortiert nach Risiko – Grundlage für Objektschutz-Gespräche.

Alle Regeln, Matrix und Schutzziele: `config/pilot.yaml`, Abschnitt `risiko`.

## 7a. Grundstücks-Steckbrief (M3)
Baut auf den fertigen Blockergebnissen auf (`preprocessing/run_steckbrief.py`), die
Hydraulik wird nicht neu gerechnet.

- **Flurstück:** ALKIS-Flurstück, in dem der repräsentative Punkt des Gebäudes liegt.
- **Wasser auf dem Grundstück:** aus den Tiefenrastern (1 m) je Szenario: größte Tiefe
  und Flächen ≥ 10 cm bzw. ≥ 30 cm auf dem Flurstück ohne Gebäudeflächen. Flurstücke, die
  über Blockgrenzen reichen, werden zusammengeführt.
- **Einzugsgebiet („Woher kommt das Wasser?“):** DGM1 mit eingebrannten Gebäuden (+5 m),
  Senken per Priority-Flood mit kleinem Gefälle (ε = 10⁻⁵ m) gefüllt – entspricht dem
  Starkregen-Fall, in dem Mulden vollgelaufen sind und überlaufen. Fließrichtung D8; das
  Einzugsgebiet eines Gebäudes sind alle Zellen, deren Fließweg durch Grundriss oder 2-m-Ring
  führt (Breitensuche gegen die Fließrichtung). Angegeben werden Fläche und der Anteil
  außerhalb des eigenen Flurstücks. Gerechnet je Block auf Kern + 1 km Puffer; reicht das
  Gebiet bis an den Rand, wird „mindestens“ angezeigt. Kanalnetz und Durchlässe sind nicht
  berücksichtigt (reine Geländebetrachtung).
- **Gebäudehöhe (3D):** Median von DOM1 − DGM1 (LGLN) im Grundriss ohne 1-m-Rand;
  Begrenzung auf 6 m (Nebengebäude S1) bzw. 30 m, da Bäume über kleinen Gebäuden das DOM
  verfälschen. Fehlwerte: 3 m (S1) bzw. 7 m.
- **Selbstschutz-Tipps:** nach Wasserstand am Haus im gewählten Szenario (Stufen wie
  Gefährdung), ergänzt um Hinweise zu kleiner Mulde am Haus, hohem Fremdzufluss
  (§ 37 WHG) und S4-Einrichtungen.

## 8. Grenzen (gehören in den Hinweistext der App)
- Statisch: keine Ganglinien, keine Fließzeiten, kein Rückstau, kein Impuls.
  Die Regendauer wirkt nur über Kanalabschlag und Infiltration.
- Fließtiefe ist eine Näherung für Normalabfluss, kein 2D-Ergebnis.
- Bordsteine (10–12 cm) im DGM1 kaum abgebildet.
- Innenhöfe ohne Durchfahrt in OSM erscheinen als abflusslose Senken.
- Kein Zufluss von außerhalb des Rechengebiets (1 km Puffer).
- Kanalnetz nur als pauschaler Abschlag.
- Einzugsgebiete rein topographisch (D8, Senken gefüllt); 3D-Gebäudehöhen genähert.

## Ausbaustufen (Architektur vorgesehen)
- Hydrodynamisches 2D-Modell (local inertial, Bates et al. 2010) als Ersatz für
  Schritt 3/4. Gleiche Ein- und Ausgaberaster.
- Versiegelung aus Copernicus HRL Imperviousness bzw. ALKIS, Böden aus BÜK50 (LBEG).
- Kanalnetz-Kopplung: Schächte als Ein- und Austrittspunkte.
