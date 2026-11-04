// PDF-Export: Gebäude-Steckbrief (A4 hoch) und Kartenausschnitt (A4 quer).
// jsPDF wird erst beim ersten Export geladen, damit die App schnell startet.
import { parse, GEF_TITEL, GEF_TXT, RISK_TXT, S_TXT } from './info.js';
import { tipsList, TIPP_STUFE, flaeche } from './steckbrief.js';

const GEF_RGB = [[58, 157, 93], [242, 183, 5], [232, 89, 12], [165, 15, 21]];
const TEXT = [27, 31, 36];
const MUTED = [93, 102, 112];
const BLAU = [11, 47, 122];

// Standardschrift (Helvetica) kennt nur Latin-1: Sonderzeichen ersetzen
const clean = (t) => String(t ?? '').replace(/≥/g, '>=').replace(/≤/g, '<=').replace(/[−–—]/g, '-')
  .replace(/≈/g, 'ca. ').replace(/[„“”]/g, '"').replace(/[‚‘’]/g, "'").replace(/…/g, '...')
  .replace(/[^\x20-\xFF\n]/g, '');

// Farbe aus '#rrggbb' oder 'rgba(r,g,b,a)' – Transparenz auf weißen Grund umgerechnet
const hex = (h) => {
  if (h.startsWith('#')) return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [r, g, b, a = 1] = h.match(/[\d.]+/g).map(Number);
  return [r, g, b].map((c) => Math.round(c * a + 255 * (1 - a)));
};

export function captureMap(map) {
  return new Promise((resolve) => {
    map.once('render', () => {
      try { resolve(map.getCanvas().toDataURL('image/jpeg', 0.88)); } catch { resolve(null); }
    });
    map.triggerRepaint();
  });
}

function writer(doc, meta) {
  const M = 15;
  const W = doc.internal.pageSize.getWidth();
  const H = doc.internal.pageSize.getHeight();
  const w = { y: M, M, W, H, inner: W - 2 * M };
  w.need = (h) => { if (w.y + h > H - 16) { doc.addPage(); w.y = M; } };
  w.text = (str, { size = 10, bold = false, color = TEXT, gap = 1.6, x = M, width = w.inner } = {}) => {
    doc.setFont('helvetica', bold ? 'bold' : 'normal');
    doc.setFontSize(size);
    doc.setTextColor(...color);
    const lines = doc.splitTextToSize(clean(str), width);
    const lh = size * 0.3528 * 1.3;
    for (const line of lines) {
      w.need(lh);
      doc.text(line, x, w.y + lh * 0.78);
      w.y += lh;
    }
    w.y += gap;
  };
  w.heading = (str) => { w.y += 2; w.need(14); w.text(str, { size: 12.5, bold: true, color: BLAU, gap: 1.2 }); };
  w.bullets = (items) => items.forEach((t) => {
    const y0 = w.y;
    w.text(t, { x: M + 5, width: w.inner - 5, gap: 0.8 });
    doc.setFillColor(...TEXT);
    doc.circle(M + 1.6, y0 + 2.4, 0.7, 'F');
  });
  w.box = (rgb, title, sub, dark = false) => {
    w.need(17);
    doc.setFillColor(...rgb);
    doc.roundedRect(M, w.y, w.inner, 15, 2, 2, 'F');
    const c = dark ? TEXT : [255, 255, 255];
    doc.setTextColor(...c);
    doc.setFont('helvetica', 'bold'); doc.setFontSize(12.5);
    doc.text(clean(title), M + 4, w.y + 6.3);
    doc.setFont('helvetica', 'normal'); doc.setFontSize(9.5);
    doc.text(clean(sub), M + 4, w.y + 11.6);
    w.y += 18;
  };
  w.table = (head, rows, cols, cur = -1) => {
    const lh = 6.2;
    const row = (cells, bold, dots) => {
      w.need(lh);
      let x = M;
      cells.forEach((c, i) => {
        doc.setFont('helvetica', bold ? 'bold' : 'normal');
        doc.setFontSize(9.5);
        doc.setTextColor(...(bold && dots === undefined ? MUTED : TEXT));
        let tx = x + 1;
        if (dots && dots[i] != null) {
          doc.setFillColor(...GEF_RGB[dots[i]]);
          doc.circle(x + 2.2, w.y + lh / 2, 1.3, 'F');
          tx = x + 5;
        }
        doc.text(clean(c), tx, w.y + lh * 0.68);
        x += cols[i];
      });
      w.y += lh;
      doc.setDrawColor(217, 222, 229);
      doc.line(M, w.y, M + cols.reduce((a, b) => a + b, 0), w.y);
    };
    row(head, true);
    rows.forEach((r, k) => row(r.cells, k === cur, r.dots));
    w.y += 3;
  };
  w.image = (data, maxH) => {
    if (!data) return;
    const p = doc.getImageProperties(data);
    let iw = w.inner;
    let ih = (p.height / p.width) * iw;
    if (ih > maxH) { ih = maxH; iw = (p.width / p.height) * ih; }
    w.need(ih + 2);
    doc.addImage(data, 'JPEG', M + (w.inner - iw) / 2, w.y, iw, ih);
    doc.setDrawColor(217, 222, 229);
    doc.rect(M + (w.inner - iw) / 2, w.y, iw, ih);
    w.y += ih + 2;
  };
  w.footer = () => {
    const n = doc.getNumberOfPages();
    for (let i = 1; i <= n; i++) {
      doc.setPage(i);
      doc.setFont('helvetica', 'normal'); doc.setFontSize(7.5); doc.setTextColor(...MUTED);
      doc.text(clean(`Starkregen-Spiegel ${meta.name || ''} - Orientierungshilfe, keine amtliche Auskunft, keine Haftung. `
        + `Berechnungsstand ${meta.stand}. Daten: DGM1, DOM1, ALKIS (c) LGLN, dl-de/by-2-0; Karte basemap.de (c) BKG.`), M, H - 8,
      { maxWidth: W - 2 * M - 18 });
      doc.text(`Seite ${i}/${n}`, W - M, H - 8, { align: 'right' });
    }
  };
  return w;
}

async function newDoc(orientation) {
  const { jsPDF } = await import('jspdf');
  return new jsPDF({ orientation, unit: 'mm', format: 'a4', compress: true });
}

const today = () => new Date().toLocaleDateString('de-DE');
const fileSafe = (s) => clean(s).replace(/[^A-Za-z0-9ÄÖÜäöüß_-]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');

// ---------------------------------------------------------------------------
export async function buildingPdf({ f, meta, sid, sb, map, markiert }) {
  const p = f.properties;
  const sc = meta.szenarien.find((x) => x.id === sid);
  const cur = parse(p[sid]);
  const img = await captureMap(map);
  const doc = await newDoc('portrait');
  const w = writer(doc, meta);

  w.text('Starkregen-Steckbrief', { size: 10, bold: true, color: BLAU, gap: 0.5 });
  w.text(p.a || 'Gebäude ohne Adresse', { size: 18, bold: true, gap: 0.5 });
  w.text(`${p.art}${sb?.fs ? ` · ${sb.fs.t}${sb.fs.fl ? ` (${sb.fs.fl.toLocaleString('de-DE')} m²)` : ''}` : ''}`,
    { size: 9.5, color: MUTED, gap: 0.5 });
  w.text(`Erstellt am ${today()} · Berechnungsstand ${meta.stand}`, { size: 8.5, color: MUTED, gap: 3 });

  w.box(GEF_RGB[cur.gef], `${GEF_TITEL[cur.gef]} (max. ${cur.cm} cm)`,
    `bei ${sc.mm} mm Regen in ${sc.dauer_h} h${sc.jaehrlichkeit ? ` - ${sc.jaehrlichkeit} (KOSTRA-DWD-2020)` : ''}`, cur.gef === 1);
  if (markiert) {
    w.text(`Rot markiert: höchstes Schutzgut (S4) mit mindestens 30 cm Wasser am Haus schon bei ${markiert.mm} mm in 1 h `
      + `(${markiert.jaehrlichkeit || ''}).`, { bold: true, color: [215, 48, 31], gap: 2 });
  }
  if (img) {
    w.image(img, 95);
    w.text(`Kartenausschnitt beim Export: Wassertiefe und Fließwege bei ${sc.mm} mm, gewähltes Gebäude schwarz umrandet.`,
      { size: 8, color: MUTED, gap: 2 });
  }
  if (p.s != null) {
    w.heading(`Schadenspotenzial ${S_TXT[p.s]}`);
    w.text(p.sg || '', { gap: 1 });
    const zsc = meta.szenarien.find((x) => x.id === p.zz);
    if (zsc) {
      const ok = parse(p[p.zz]).gef === 0;
      w.text(`Schutzziel (in Anlehnung an DWA-A 118): trocken bis ${zsc.mm} mm in ${zsc.dauer_h} h - `
        + `${ok ? 'nach der Berechnung erreicht.' : `nicht erreicht (am Haus ${GEF_TXT[parse(p[p.zz]).gef]}).`}`,
      { bold: true, color: ok ? [58, 157, 93] : [215, 48, 31] });
    }
  }

  w.heading('Wasser am Gebäude je Regenmenge');
  const rows = meta.szenarien.map((x) => {
    const r = parse(p[x.id]);
    return { cells: [`${x.mm} mm/${x.dauer_h} h`, x.jaehrlichkeit || '', GEF_TXT[r.gef], p.s != null ? RISK_TXT[r.risk] : '-', `${r.cm} cm`],
      dots: [null, null, r.gef, p.s != null ? r.risk : null, null] };
  });
  w.table(['Regen', 'Jährlichkeit', 'Wasser am Haus', 'Risiko', 'max. am Haus'], rows, [26, 52, 38, 32, 30],
    meta.szenarien.findIndex((x) => x.id === sid));
  w.text('"max. am Haus" = höchster Wert im 2-m-Streifen rund um das Gebäude.', { size: 8, color: MUTED });
  if (cur.mulde) {
    w.text('Direkt am Haus gibt es eine kleine, tiefe Mulde (z. B. Kellerabgang, Lichtschacht, abgesenkte Einfahrt). '
      + 'Dort kann sich Wasser sammeln und ins Haus laufen.', { bold: true });
  }

  const e = sb?.e;
  if (e) {
    w.heading('Woher kommt das Wasser?');
    const min = e.t ? 'mindestens ' : '';
    w.text(e.g ? `Bei Starkregen kann Wasser von ${min}${flaeche(e.a)} über das Gelände zu diesem Gebäude fließen - `
      + `davon ${e.x} % von außerhalb des eigenen Flurstücks.`
      : `Zum Gebäude fließt fast nur der Regen vom eigenen Dach und aus dem direkten Umfeld (${flaeche(e.a)}).`);
    w.text('Geländeanalyse (DGM1, Gebäude als Hindernis, Mulden als vollgelaufen angenommen), ohne Kanalnetz.',
      { size: 8, color: MUTED });
  }
  const fs = sb?.fs;
  if (fs && meta.szenarien.some((x) => fs[x.id])) {
    w.heading('Wasser auf dem Grundstück (Flurstück)');
    w.text(`${fs.t}${fs.fl ? `, ${fs.fl.toLocaleString('de-DE')} m²` : ''}`, { size: 9.5 });
    w.table(['Regen', 'max. Tiefe', 'Fläche ab 10 cm', 'Fläche ab 30 cm'], meta.szenarien.filter((x) => fs[x.id]).map((x) => {
      const [cm, a10, a30] = fs[x.id];
      return { cells: [`${x.mm} mm`, cm < 5 ? 'trocken' : `${cm} cm`, a10 ? `${a10.toLocaleString('de-DE')} m²` : '-',
        a30 ? `${a30.toLocaleString('de-DE')} m²` : '-'] };
    }), [26, 34, 44, 44], meta.szenarien.findIndex((x) => x.id === sid));
    w.text('Ohne Gebäudeflächen. Das Flurstück ist nicht immer identisch mit dem Grundstück im Grundbuch.', { size: 8, color: MUTED });
  }

  w.heading(`Was Sie tun können - ${TIPP_STUFE[cur.gef]}`);
  w.bullets(tipsList(p, cur, sb));
  w.text('Mehr dazu: HochwasserPass (hochwasser-pass.com), Bundesamt für Bevölkerungsschutz (bbk.bund.de).', { size: 8.5, color: MUTED });

  w.heading('Hinweis');
  w.text('Vereinfachte Berechnung (Geländeanalyse mit Füll-und-Überlauf-Verfahren), kein hydrodynamisches Modell. '
    + `Kanalnetz pauschal (${meta.kanal_mm_h ?? ''} mm/h), Versickerung nach Bodenart. Bordsteine, Mauern und Lichtschächte sind im `
    + 'Geländemodell nur teilweise enthalten, Keller nicht bekannt. Tatsächliche Wasserstände können deutlich abweichen. '
    + 'Amtliche Informationen: Hinweiskarte Starkregengefahren (BKG/NLWKN) und die Stadtverwaltung.', { size: 8.5, color: MUTED });
  w.footer();
  doc.save(`Starkregen-Steckbrief_${fileSafe(p.a || `Gebaeude_${p.id}`)}.pdf`);
}

// ---------------------------------------------------------------------------
export async function mapPdf({ map, meta, sid, viewTitle, legend }) {
  const sc = meta.szenarien.find((x) => x.id === sid);
  const img = await captureMap(map);
  const doc = await newDoc('landscape');
  const w = writer(doc, meta);
  w.text(`Starkregen-Spiegel ${meta.name || ''} - Kartenausschnitt`, { size: 15, bold: true, gap: 0.5 });
  w.text(`${viewTitle} · Regen ${sc.mm} mm in ${sc.dauer_h} h${sc.jaehrlichkeit ? ` (${sc.jaehrlichkeit}, KOSTRA-DWD-2020)` : ''} · `
    + `erstellt am ${today()}`, { size: 9.5, color: MUTED, gap: 2 });
  const top = w.y;
  const legW = 62;
  const p = img ? doc.getImageProperties(img) : null;
  const availW = w.inner - legW - 5;
  const availH = w.H - top - 18;
  if (p) {
    let iw = availW;
    let ih = (p.height / p.width) * iw;
    if (ih > availH) { ih = availH; iw = (p.width / p.height) * ih; }
    doc.addImage(img, 'JPEG', w.M, top, iw, ih);
    doc.setDrawColor(217, 222, 229);
    doc.rect(w.M, top, iw, ih);
  }
  // Legende rechts
  const x = w.M + availW + 5;
  let y = top + 2;
  const item = (rgb, txt, line = false) => {
    if (line) { doc.setDrawColor(...rgb); doc.setLineWidth(0.9); doc.rect(x, y - 2.8, 5, 3.6); doc.setLineWidth(0.2); } else {
      doc.setFillColor(...rgb); doc.rect(x, y - 2.8, 5, 3.6, 'F');
    }
    doc.setFont('helvetica', 'normal'); doc.setFontSize(8.5); doc.setTextColor(...TEXT);
    const lines = doc.splitTextToSize(clean(txt), legW - 8);
    doc.text(lines, x + 7, y);
    y += 4.4 * lines.length + 0.8;
  };
  const head = (t) => { doc.setFont('helvetica', 'bold'); doc.setFontSize(9.5); doc.setTextColor(...BLAU); doc.text(clean(t), x, y); y += 5; };
  for (const g of legend) {
    head(g.titel);
    g.eintraege.forEach((e) => item(hex(e.farbe), e.text, e.rahmen));
    y += 2;
  }
  doc.setFont('helvetica', 'normal'); doc.setFontSize(7.5); doc.setTextColor(...MUTED);
  doc.text(doc.splitTextToSize(clean('Vereinfachte Berechnung, keine amtliche Starkregengefahrenkarte. Tatsächliche Wasserstände '
    + 'können deutlich abweichen.'), legW), x, y + 2);
  w.footer();
  doc.save(`Starkregen-Karte_${sc.mm}mm_${new Date().toISOString().slice(0, 10)}.pdf`);
}
