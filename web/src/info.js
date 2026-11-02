// Kurzinfo zu Gebäude bzw. Senke (Vorstufe des Grundstücks-Steckbriefs aus Meilenstein 3)

const AMPEL_TXT = [
  { cls: 'gruen', kurz: 'gering', titel: 'Geringe Gefährdung', text: 'Am Gebäude sammelt sich nach der Berechnung kaum Wasser (unter 10 cm).' },
  { cls: 'gelb', kurz: 'mittel', titel: 'Mittlere Gefährdung', text: 'Am Gebäude können 10–30 cm Wasser stehen oder vorbeifließen.' },
  { cls: 'rot', kurz: 'hoch', titel: 'Hohe Gefährdung', text: 'Am Gebäude können mehr als 30 cm Wasser stehen oder vorbeifließen.' },
];

const RISK_TXT = ['gering', 'mäßig', 'hoch', 'sehr hoch'];
const S_TXT = { 1: 'S1 – gering', 2: 'S2 – mäßig', 3: 'S3 – hoch', 4: 'S4 – sehr hoch' };
const GEF_TXT = ['unter 10 cm', '10–30 cm', '30–50 cm', 'über 50 cm'];

export function parse(v) {
  const [a, cm, mulde, gef, risk] = (v || '0|0|0|0|0').split('|').map(Number);
  return { ampel: a, cm, mulde: mulde === 1, gef: gef || 0, risk: risk || 0 };
}

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export function renderBuilding(f, meta, sid, view = 'gef') {
  const p = f.properties;
  const cur = parse(p[sid]);
  const a = AMPEL_TXT[cur.ampel];
  const sc = meta.szenarien.find((x) => x.id === sid);
  const hasRisk = p.s != null;
  const rows = meta.szenarien.map((x) => {
    const r = parse(p[x.id]);
    return `<tr class="${x.id === sid ? 'cur' : ''}"><td>${x.mm} mm</td>
      <td><span class="dot ${AMPEL_TXT[r.ampel].cls}"></span>${AMPEL_TXT[r.ampel].kurz}</td>
      ${hasRisk ? `<td><span class="dot r${r.risk}"></span>${RISK_TXT[r.risk]}</td>` : ''}
      <td>${r.cm} cm</td></tr>`;
  }).join('');
  let schutz = '';
  if (hasRisk) {
    const zsc = meta.szenarien.find((x) => x.id === p.zz);
    const zr = parse(p[p.zz]);
    const ok = zr.gef === 0;
    schutz = `
    <div class="schutz"><strong>Schutzwürdigkeit ${S_TXT[p.s]}</strong><br>${esc(p.sg)}</div>
    ${zsc ? `<p class="ziel ${ok ? 'ja' : 'nein'}">Schutzziel (in Anlehnung an DWA-A 118): trocken bis ${zsc.mm} mm in ${zsc.dauer_h} h –
      ${ok ? 'nach der Berechnung erreicht.' : `nicht erreicht (am Haus ${GEF_TXT[zr.gef]}).`}</p>` : ''}`;
  }
  const head = view === 'risiko' && hasRisk
    ? `<div class="risk r${cur.risk}"><strong>Risiko ${RISK_TXT[cur.risk]}</strong>
        <span>Gefährdung am Haus ${GEF_TXT[cur.gef]} × Schutzwürdigkeit ${S_TXT[p.s]} – bei ${sc.mm} mm in ${sc.dauer_h} h</span></div>`
    : `<div class="ampel ${a.cls}"><strong>${a.titel}</strong><span>bei ${sc.mm} mm Regen in ${sc.dauer_h} h</span></div>
       <p>${a.text}</p>`;
  return `
    <h2>${esc(p.a) || 'Gebäude ohne Adresse'}</h2>
    <p class="muted">${esc(p.art)}</p>
    ${head}
    ${cur.mulde ? '<p class="hint">⚠ Direkt am Haus gibt es eine <strong>kleine, tiefe Mulde</strong> (z. B. Kellerabgang, Lichtschacht, abgesenkte Einfahrt). Dort kann sich Wasser sammeln und ins Haus laufen.</p>' : ''}
    ${schutz}
    <table class="scen"><thead><tr><th>Regen</th><th>Gefährdung</th>${hasRisk ? '<th>Risiko</th>' : ''}<th>max. am Haus</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="small">„max. am Haus“ = höchster Wert im 2-m-Streifen rund um das Gebäude. Schutzwürdigkeit aus öffentlichen
      ALKIS-Daten (Gebäudefunktion, Flächennutzung); Keller sind darin nicht enthalten. Orientierungshilfe, keine amtliche Auskunft.</p>`;
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
