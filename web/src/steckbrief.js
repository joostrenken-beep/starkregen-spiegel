// Grundstücks-Steckbrief (Meilenstein 3): Flurstück, Wasser auf dem Grundstück,
// Einzugsgebiet ("Woher kommt das Wasser?") und Selbstschutz-Tipps.

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const n0 = (v) => Number(v).toLocaleString('de-DE', { maximumFractionDigits: 0 });

export function flaeche(m2) {
  if (m2 >= 10000) return `${(m2 / 10000).toLocaleString('de-DE', { maximumFractionDigits: 1 })} ha`;
  return `${n0(Math.round(m2 / 10) * 10 || m2)} m²`;
}

function cmTxt(cm) {
  if (cm < 5) return 'trocken';
  return `${cm} cm`;
}

export function parcelSection(sb, meta, sid) {
  const fs = sb?.fs;
  if (!fs) return '';
  const rows = meta.szenarien.map((x) => {
    const v = fs[x.id];
    if (!v) return '';
    const [cm, a10, a30] = v;
    const pct = fs.fl ? Math.round((a10 / fs.fl) * 100) : null;
    return `<tr class="${x.id === sid ? 'cur' : ''}"><td>${x.mm} mm</td><td>${cmTxt(cm)}</td>
      <td>${a10 ? `${n0(a10)} m²${pct != null ? ` (${pct} %)` : ''}` : '–'}</td><td>${a30 ? `${n0(a30)} m²` : '–'}</td></tr>`;
  }).join('');
  const cur = fs[sid];
  const sum = cur ? (cur[1] > 0 ? `bis ${cmTxt(cur[0])}, ${n0(cur[1])} m² nass` : 'kaum Wasser') : '';
  return `<details class="sec">
    <summary><span>Grundstück</span><span class="sum">${sum}</span></summary>
    <p>${esc(fs.t)}${fs.fl ? ` · ${n0(fs.fl)} m²` : ''}</p>
    <table class="scen"><thead><tr><th>Regen</th><th>max. Tiefe</th><th>ab 10 cm</th><th>ab 30 cm</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <p class="small">Werte für das ganze ALKIS-Flurstück, ohne Gebäudeflächen. Ein Flurstück kann
      mehrere Gebäude tragen und ist nicht immer identisch mit dem Grundstück im Grundbuch.</p>
  </details>`;
}

export function catchmentSection(sb, shown) {
  const e = sb?.e;
  if (!e) return '';
  const min = e.t ? 'mindestens ' : '';
  if (!e.g) {
    return `<details class="sec">
      <summary><span>Woher kommt das Wasser?</span><span class="sum">kein Zufluss von außen</span></summary>
      <p>Zum Gebäude fließt nach der Geländeanalyse fast nur der Regen vom eigenen Dach und aus dem direkten
        Umfeld (${flaeche(e.a)}). Wasser am Haus stammt dann vor allem aus Rückstau oder aus der Mulde selbst.</p>
    </details>`;
  }
  return `<details class="sec" open>
    <summary><span>Woher kommt das Wasser?</span><span class="sum">${min}${flaeche(e.a)}</span></summary>
    <p>Bei Starkregen kann Wasser von ${min}<strong>${flaeche(e.a)}</strong> über das Gelände zu diesem Gebäude fließen –
      davon <strong>${e.x} %</strong> von außerhalb des eigenen Flurstücks.</p>
    <button type="button" class="secondary" data-action="einzug" aria-pressed="${shown}">
      ${shown ? 'Einzugsgebiet ausblenden' : 'Einzugsgebiet auf der Karte zeigen'}</button>
    <p class="small">Berechnet über das Geländemodell (DGM1) mit Gebäuden als Hindernis; Mulden werden als
      vollgelaufen angenommen (Starkregen-Fall), das Kanalnetz nicht berücksichtigt.${e.t ? ' Das tatsächliche Gebiet ist größer – die Berechnung wurde hier begrenzt.' : ''}</p>
  </details>`;
}

// ---------------------------------------------------------------------------
// Selbstschutz – nach Wasserstand am Haus (Gefährdungsstufe 0–3) des gewählten Szenarios
const T_ALLE = [
  'Warn-App installieren (z. B. NINA oder DWD WarnWetter) und Unwetterwarnungen ernst nehmen.',
  'Räume unter der Straße (Keller) gegen Rückstau aus dem Kanal sichern: Rückstauverschluss oder Hebeanlage – und regelmäßig warten lassen.',
  'Versicherungsschutz gegen Elementarschäden (Starkregen, Überschwemmung) prüfen.',
  'Bei Starkregen Keller, Tiefgaragen und Unterführungen nicht betreten – Strom- und Ertrinkungsgefahr.',
];
const T_GELB = [
  'Kellerabgänge, Lichtschächte und Eingänge mit Schwellen oder Aufkantungen versehen (mindestens 15–20 cm über Gelände).',
  'Gelände und Pflaster vom Haus weg neigen, Abläufe vor Kellertreppen und Garagen freihalten.',
  'Mobile Barrieren oder Sandsäcke für Türen und Kellerfenster bereithalten.',
  'Wertsachen, Dokumente und empfindliche Geräte nicht im Keller lagern.',
];
const T_ROT = [
  'Objektschutz fachlich planen lassen: z. B. druckwasserdichte Kellerfenster, Dammbalken an Türen – Schutzhöhe über dem berechneten Wasserstand.',
  'Heizung, Stromverteilung und Öltank hochwassersicher machen (höher setzen, Öltank gegen Aufschwimmen sichern).',
  'Notfallplan aufstellen: Was wird zuerst hochgeräumt, wo werden Strom und Heizung abgeschaltet, wer hilft?',
];
const T_MULDE = 'Direkt am Haus liegt eine kleine, tiefe Mulde (z. B. Kellerabgang, Lichtschacht, abgesenkte Einfahrt): dort besonders auf Schwellen, Abdeckungen und freie Abläufe achten.';
const T_AUSSEN = 'Ein großer Teil des Wassers kommt von außerhalb Ihres Flurstücks. Sprechen Sie mit Nachbarn und Stadt über gemeinsame Notabflusswege. Eigene Maßnahmen dürfen das Wasser nicht zum Nachteil der Nachbarn umleiten (§ 37 Wasserhaushaltsgesetz).';
const T_S4 = 'Einrichtung mit besonders schutzbedürftigen Menschen: Alarm- und Räumungsplan für Starkregen aufstellen, Zuständigkeiten festlegen und mit Feuerwehr und Stadt abstimmen.';

export function tipsSection(p, cur, sb, sc) {
  const lvl = cur.gef;
  const items = [];
  if (lvl >= 2) items.push(...T_ROT);
  if (lvl >= 1) items.push(...T_GELB);
  if (cur.mulde) items.push(T_MULDE);
  if (sb?.e?.g && sb.e.x >= 50 && lvl >= 1) items.push(T_AUSSEN);
  if (p.s === 4) items.push(T_S4);
  items.push(...T_ALLE);
  const sum = ['Vorsorge allgemein', 'einfache Maßnahmen', 'Objektschutz empfohlen', 'Objektschutz dringend'][lvl];
  return `<details class="sec"${lvl >= 1 ? ' open' : ''}>
    <summary><span>Was Sie tun können</span><span class="sum">${sum}</span></summary>
    <p class="small">Passend zum Wasser am Haus bei ${sc.mm} mm in ${sc.dauer_h} h.</p>
    <ul class="tips">${items.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>
    <p class="small">Mehr dazu: <a href="https://hochwasser-pass.com" target="_blank" rel="noopener">HochwasserPass</a>
      (Check durch Sachkundige), <a href="https://www.bbk.bund.de" target="_blank" rel="noopener">Bundesamt für Bevölkerungsschutz</a>.</p>
  </details>`;
}
