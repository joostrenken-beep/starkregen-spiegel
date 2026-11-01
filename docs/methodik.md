# Methodik Starkregen-Spiegel (Stand M1)

Vereinfachte topografische Gefährdungsanalyse (vgl. DWA-M 119), ergänzt um eine
Näherung der Fließtiefe auf geneigten Flächen. **Kein hydrodynamisches 2D-Modell.**

## 1. Geländeaufbereitung
- DGM1 (LGLN), 1 m.
- **Gebäude einbrennen:** OSM-Grundrisse + 5 m → Wasser fließt um Häuser herum.
- **Durchlässe öffnen:** OSM `waterway` + `tunnel=culvert` → Zellen entlang der
  Leitung auf eine Linie zwischen Ein- und Auslaufsohle absenken. Sonst wirken
  Straßendämme als Staumauern.

## 2. Abflussbildung (Blockregen P in T)
Je Zelle Versiegelungsgrad *s* (0…1) aus OSM: Straßen (Achse + Breite je Klasse)
und Parkplätze = 1, Wohngebiet 0,35, Gewerbe/Industrie 0,7, sonst 0.

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

## 5. Grenzen (gehören in den Hinweistext der App)
- Statisch: keine Ganglinien, keine Fließzeiten, kein Rückstau, kein Impuls.
  Die Regendauer wirkt nur über Kanalabschlag und Infiltration.
- Fließtiefe ist eine Näherung für Normalabfluss, kein 2D-Ergebnis.
- Bordsteine (10–12 cm) im DGM1 kaum abgebildet.
- Innenhöfe ohne Durchfahrt in OSM erscheinen als abflusslose Senken.
- Kein Zufluss von außerhalb des Rechengebiets (1 km Puffer).
- Kanalnetz nur als pauschaler Abschlag.

## Ausbaustufen (Architektur vorgesehen)
- Hydrodynamisches 2D-Modell (local inertial, Bates et al. 2010) als Ersatz für
  Schritt 3/4. Gleiche Ein- und Ausgaberaster.
- Versiegelung aus Copernicus HRL Imperviousness bzw. ALKIS, Böden aus BÜK50 (LBEG).
- Kanalnetz-Kopplung: Schächte als Ein- und Austrittspunkte.
