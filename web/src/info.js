// Kurzinfo zu Gebäude bzw. Senke (Vorstufe des Grundstücks-Steckbriefs aus Meilenstein 3)

const AMPEL_TXT = [
  { cls: 'gruen', kurz: 'gering', titel: 'Geringe Gefährdung', text: 'Am Gebäude sammelt sich nach der Berechnung kaum Wasser (unter 10 cm).' },
  { cls: 'gelb', kurz: 'mittel', titel: 'Mittlere Gefährdung', text: 'Am Gebäude können 10–30 cm Wasser stehen oder vorbeifließen.' },
  { cls: 'rot', kurz: 'hoch', titel: 'Hohe Gefährdung', text: 'Am Gebäude können mehr als 30 cm Wasser stehen oder vorbeifließen.' },
];

export function parse(v) {
  const [a, cm, mulde] = (v || '0|0|0').split('|').map(Number);
  return { ampel: a, cm, mulde: mulde === 1 };
}

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export function renderBuilding(f, meta, sid) {
  const p = f.properties;
  const cur = parse(p[sid]);
  const a = AMPEL_TXT[cur.ampel];
  const sc = meta.szenarien.find((x) => x.id === sid);
  const rows = meta.szenarien.map((x) => {
    const r = parse(p[x.id]);
    return `<tr class="${x.id === sid ? 'cur' : ''}"><td>${x.mm} mm</td>
      <td><span class="dot ${AMPEL_TXT[r.ampel].cls}"></span>${AMPEL_TXT[r.ampel].kurz}</td>
      <td>${r.cm} cm</td></tr>`;
  }).join('');
  return `
    <h2>${esc(p.a) || 'Gebäude ohne Adresse'}</h2>
    <p class="muted">${esc(p.art)}</p>
    <div class="ampel ${a.cls}"><strong>${a.titel}</strong><span>bei ${sc.mm} mm Regen in ${sc.dauer_h} h</span></div>
    <p>${a.text}</p>
    ${cur.mulde ? '<p class="hint">⚠ Direkt am Haus gibt es eine <strong>kleine, tiefe Mulde</strong> (z. B. Kellerabgang, Lichtschacht, abgesenkte Einfahrt). Dort kann sich Wasser sammeln und ins Haus laufen.</p>' : ''}
    <table class="scen"><thead><tr><th>Regen</th><th>Ampel</th><th>max. am Haus</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="small">„max. am Haus“ = höchster Wert im 2-m-Streifen rund um das Gebäude. Orientierungshilfe, keine amtliche Auskunft.</p>`;
}

export function renderSink(p, meta, sid) {
  const sc = meta.szenarien.find((x) => x.id === sid);
  const f = Math.round((p[`f_${sid}`] ?? 0) * 100);
  return `
    <h2>Senke (Geländemulde)</h2>
    <p>Hier kann sich Wasser sammeln: bis zu <strong>${Number(p.tmax).toFixed(2).replace('.', ',')} m</strong> tief,
      Fassungsvermögen ca. <strong>${Number(p.vol).toLocaleString('de-DE')} m³</strong>.</p>
    <p>Bei ${sc.mm} mm Regen ist sie nach der Berechnung zu etwa <strong>${f} %</strong> gefüllt.</p>`;
}
