// Draws the order in the layout of the printed Skipper Hosiery ORDER FORM and
// returns a canvas that can be exported as JPEG.
import { ADULT_SIZES, KIDS_SIZES, PRICE_LIST_DATE } from './catalog.js';
import { lineTotals, lineRate, orderTotals, unitOf, inr } from './order.js';

export const COMPANY = {
  name: 'SKIPPER HOSIERY PVT. LTD.',
  lines: ['158, JAMUNALAL BAJAJ STREET', 'KOLKATA - 700 007'],
  contact: 'Dial : 2268 8860, 4004 6001   WhatsApp : 9836712349',
  email: 'E-mail : info@rdknits.com',
};

const W = 1240;
const M = 56;               // page margin
const PRINT = '#1f2f8f';    // printed-form blue
const INK = '#1a3fbf';      // ball-pen blue for handwritten values
const HAND = '"Kalam", "Segoe Print", "Bradley Hand", cursive';
const SANS = 'Arial, Helvetica, sans-serif';

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function text(ctx, s, x, y, { font = `16px ${SANS}`, color = PRINT, align = 'left', base = 'alphabetic', maxW } = {}) {
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = base;
  if (maxW) ctx.fillText(String(s), x, y, maxW);
  else ctx.fillText(String(s), x, y);
}

function hline(ctx, x1, x2, y, w = 1.4, color = PRINT) {
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.beginPath();
  ctx.moveTo(x1, y);
  ctx.lineTo(x2, y);
  ctx.stroke();
}

function vline(ctx, x, y1, y2, w = 1.4) {
  ctx.strokeStyle = PRINT;
  ctx.lineWidth = w;
  ctx.beginPath();
  ctx.moveTo(x, y1);
  ctx.lineTo(x, y2);
  ctx.stroke();
}

function dotted(ctx, x1, x2, y) {
  ctx.save();
  ctx.setLineDash([2, 4]);
  hline(ctx, x1, x2, y, 1.2);
  ctx.restore();
}

// Fit handwriting into a width by shrinking the font.
function hand(ctx, s, x, y, size, maxW, align = 'left') {
  let sz = size;
  ctx.font = `${sz}px ${HAND}`;
  while (maxW && ctx.measureText(s).width > maxW && sz > 12) {
    sz -= 1;
    ctx.font = `${sz}px ${HAND}`;
  }
  text(ctx, s, x, y, { font: `${sz}px ${HAND}`, color: INK, align });
}

function splitShape(shape) {
  if (!shape) return [];
  const m = shape.match(/^(\S+)\s+(\(.+\))$/);
  return m ? [m[1], m[2]] : [shape];
}

export async function ensureFonts() {
  if (typeof document === 'undefined' || !document.fonts) return;
  try {
    await Promise.all([document.fonts.load(`28px Kalam`), document.fonts.load(`bold 28px Kalam`)]);
  } catch { /* fall back to system cursive */ }
}

/**
 * @param {object} order
 * @param {{withPrices?: boolean}} opts
 * @returns {HTMLCanvasElement}
 */
export function renderOrderForm(order, opts = {}) {
  const withPrices = opts.withPrices !== false;
  const adult = order.lines.filter((l) => l.section !== 'kids');
  const kids = order.lines.filter((l) => l.section === 'kids');

  const ROW = 46;
  const HEAD = 40;
  const adultRows = Math.max(10, adult.length);
  const kidsRows = Math.max(6, kids.length);

  const tableTop = 450;
  const tableH = HEAD + adultRows * ROW + HEAD + kidsRows * ROW;
  const formBottom = tableTop + tableH + 230;
  const priced = order.lines.filter((l) => lineTotals(l).boxes > 0);
  const priceH = withPrices ? 150 + priced.length * 40 + 90 : 0;
  const H = formBottom + priceH;

  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, W, H);

  // ---------- title
  text(ctx, 'ORDER FORM', W / 2, 58, { font: `bold 26px ${SANS}`, align: 'center' });

  // ---------- header box
  const hx = M;
  const hy = 80;
  const hw = W - 2 * M;
  const hh = 250;
  const split = hx + hw * 0.47;
  ctx.lineWidth = 2;
  ctx.strokeStyle = PRINT;
  roundRect(ctx, hx, hy, hw, hh, 14);
  ctx.stroke();
  vline(ctx, split, hy, hy + hh, 1.6);

  text(ctx, 'M/s.', hx + 18, hy + 60, { font: `bold 17px ${SANS}` });
  hline(ctx, hx + 66, split - 20, hy + 64);
  hline(ctx, hx + 30, split - 20, hy + 118);
  hline(ctx, hx + 30, split - 20, hy + 172);
  text(ctx, 'GSTIN :', hx + 18, hy + 228, { font: `bold 17px ${SANS}` });
  hline(ctx, hx + 100, split - 20, hy + 232);

  const c = order.customer || {};
  hand(ctx, c.name || '', hx + 80, hy + 56, 34, split - hx - 110);
  hand(ctx, c.line2 || '', hx + 60, hy + 110, 32, split - hx - 90);
  hand(ctx, c.line3 || '', hx + 60, hy + 164, 32, split - hx - 90);
  hand(ctx, c.gstin || '', hx + 112, hy + 224, 28, split - hx - 140);

  // company block
  const rx = split + 20;
  const rw = hx + hw - 20 - rx;
  ctx.fillStyle = PRINT;
  roundRect(ctx, rx, hy + 16, rw, 52, 10);
  ctx.fill();
  text(ctx, COMPANY.name, rx + rw / 2, hy + 54, { font: `bold 31px ${SANS}`, color: '#fff', align: 'center', maxW: rw - 20 });
  text(ctx, COMPANY.lines[0], rx + rw / 2, hy + 98, { font: `bold 19px ${SANS}`, align: 'center' });
  text(ctx, COMPANY.lines[1], rx + rw / 2, hy + 124, { font: `bold 19px ${SANS}`, align: 'center' });
  text(ctx, COMPANY.contact, rx + rw / 2, hy + 150, { font: `16px ${SANS}`, align: 'center', maxW: rw });
  text(ctx, COMPANY.email, rx + rw / 2, hy + 174, { font: `16px ${SANS}`, align: 'center' });
  hline(ctx, split, hx + hw, hy + 192, 1.6);
  text(ctx, 'Order No.', rx, hy + 228, { font: `16px ${SANS}` });
  dotted(ctx, rx + 78, rx + rw * 0.55, hy + 230);
  text(ctx, 'Dated', rx + rw * 0.58, hy + 228, { font: `16px ${SANS}` });
  dotted(ctx, rx + rw * 0.58 + 50, rx + rw, hy + 230);
  hand(ctx, order.orderNo || '', rx + 86, hy + 224, 28, rw * 0.55 - 90);
  hand(ctx, order.date || '', rx + rw * 0.58 + 56, hy + 224, 30, rw * 0.42 - 60);

  // ---------- "Dear Sirs" strip
  const dy = hy + hh + 14;
  ctx.lineWidth = 1.6;
  roundRect(ctx, hx, dy, hw, 76, 10);
  ctx.stroke();
  text(ctx, 'Dear Sirs,', hx + 16, dy + 28, { font: `16px ${SANS}` });
  text(ctx, 'Please supply the following goods through', hx + 110, dy + 58, { font: `16px ${SANS}` });
  hline(ctx, hx + 440, hx + hw - 110, dy + 62, 1.2);
  text(ctx, 'at our risk.', hx + hw - 100, dy + 58, { font: `16px ${SANS}` });
  hand(ctx, order.transport || '', hx + 450, dy + 54, 28, hw - 570);

  // ---------- table
  const tx = hx;
  const tw = hw;
  const descW = 300;
  const shapeW = 76;
  ctx.lineWidth = 2;
  roundRect(ctx, tx, tableTop, tw, tableH, 14);
  ctx.stroke();

  const drawSection = (y0, title, sizes, lines, rows) => {
    const cols = [...sizes.map(String), 'TOTAL'];
    const colW = (tw - descW - shapeW) / cols.length;
    const xDesc = tx;
    const xShape = tx + descW;
    const xCol = (i) => tx + descW + shapeW + i * colW;
    // header
    text(ctx, title, xDesc + descW / 2, y0 + 27, { font: `bold 17px ${SANS}`, align: 'center' });
    text(ctx, 'SHAPE', xShape + shapeW / 2, y0 + 27, { font: `bold 15px ${SANS}`, align: 'center' });
    cols.forEach((cName, i) => {
      text(ctx, cName, xCol(i) + colW / 2, y0 + 27, { font: `${cName === 'TOTAL' ? 'bold 14px' : '16px'} ${SANS}`, align: 'center' });
    });
    hline(ctx, tx, tx + tw, y0 + HEAD, 1.8);
    const yEnd = y0 + HEAD + rows * ROW;
    vline(ctx, xShape, y0, yEnd, 1.6);
    for (let i = 0; i < cols.length; i++) vline(ctx, xCol(i), y0, yEnd, 1.2);
    for (let r = 1; r <= rows; r++) hline(ctx, tx, tx + tw, y0 + HEAD + r * ROW, r === rows ? 1.8 : 1.1);

    lines.forEach((l, r) => {
      const yb = y0 + HEAD + r * ROW + ROW - 12;
      hand(ctx, l.desc || '', xDesc + 14, yb, 30, descW - 22);
      const sh = splitShape(l.shape);
      if (sh.length === 2) {
        hand(ctx, sh[0], xShape + shapeW / 2, yb - 17, 17, shapeW - 6, 'center');
        hand(ctx, sh[1], xShape + shapeW / 2, yb + 3, 16, shapeW - 6, 'center');
      } else if (sh.length) {
        hand(ctx, sh[0], xShape + shapeW / 2, yb, 24, shapeW - 8, 'center');
      }
      sizes.forEach((s, i) => {
        const q = Number(l.qty[s]) || 0;
        if (q) hand(ctx, String(q), xCol(i) + colW / 2, yb, 32, colW - 6, 'center');
      });
      // Like the paper forms: cross out 73 when the sizes around it are ordered.
      const i73 = sizes.indexOf(73);
      if (i73 >= 0 && !Number(l.qty[73]) && Number(l.qty[70]) && Number(l.qty[75])) {
        hand(ctx, 'X', xCol(i73) + colW / 2, yb, 28, colW - 6, 'center');
      }
      const t = lineTotals(l);
      if (t.boxes) hand(ctx, String(t.boxes), xCol(cols.length - 1) + colW / 2, yb, 30, colW - 6, 'center');
    });
    return yEnd;
  };

  const yKids = drawSection(tableTop, 'DESCRIPTION', ADULT_SIZES, adult, adultRows);
  drawSection(yKids, 'KIDS DESCRIPTION', KIDS_SIZES, kids, kidsRows);

  // ---------- total units
  const totals = orderTotals(order);
  const tuY = tableTop + tableH + 14;
  const tuW = 96;
  text(ctx, 'TOTAL UNITS', tx + tw - tuW - 16, tuY + 34, { font: `bold 19px ${SANS}`, align: 'right' });
  ctx.lineWidth = 1.8;
  ctx.strokeRect(tx + tw - tuW, tuY, tuW, 52);
  hand(ctx, String(totals.boxes || ''), tx + tw - tuW / 2, tuY + 40, 32, tuW - 8, 'center');

  // ---------- signatures
  const sy = formBottom - 60;
  hline(ctx, M - 20, M + 340, sy - 26, 1.4);
  text(ctx, 'Signature of Representative', M + 10, sy, { font: `18px ${SANS}` });
  hline(ctx, W - M - 360, W - M, sy - 26, 1.4);
  text(ctx, 'Signature of Customer', W - M - 300, sy, { font: `18px ${SANS}` });

  // ---------- price summary
  if (withPrices) drawPriceSummary(ctx, order, formBottom, priced, totals);

  return canvas;
}

function rateBreakdown(line) {
  // group consecutive sizes that share a rate: "85-90 @880 x4"
  const sizes = Object.keys(line.qty).map(Number).filter((s) => Number(line.qty[s]) > 0).sort((a, b) => a - b);
  const groups = [];
  for (const s of sizes) {
    const r = lineRate(line, s);
    const q = Number(line.qty[s]);
    const g = groups[groups.length - 1];
    if (g && g.rate === r) { g.to = s; g.q += q; } else groups.push({ from: s, to: s, rate: r, q });
  }
  return groups.map((g) => `${g.from === g.to ? g.from : `${g.from}-${g.to}`} ${g.rate == null ? '@ ?' : `@${g.rate}`} ×${g.q}`).join(',  ');
}

function drawPriceSummary(ctx, order, y0, lines, totals) {
  const x = M;
  const w = W - 2 * M;
  ctx.fillStyle = '#f3f5fc';
  ctx.fillRect(0, y0 - 10, W, ctx.canvas.height - y0 + 10);
  text(ctx, 'ORDER VALUE', x, y0 + 36, { font: `bold 22px ${SANS}` });
  text(ctx, `As per Price List NE dated ${PRICE_LIST_DATE}  ·  rates per box of 10 pcs incl. GST (Sofiyaa / Honey: per dozen)`,
    x + 175, y0 + 35, { font: `15px ${SANS}`, color: '#445', maxW: w - 180 });

  const cx = [x, x + 40, x + 340, x + 420, x + 510, x + w - 150];
  const hy = y0 + 76;
  const heads = ['#', 'Description', 'Shape', 'Qty', 'Size @ rate × qty', 'Amount'];
  heads.forEach((h, i) => text(ctx, h, i === 5 ? x + w : cx[i], hy, { font: `bold 15px ${SANS}`, align: i === 5 ? 'right' : 'left' }));
  hline(ctx, x, x + w, hy + 10, 1.4);

  lines.forEach((l, i) => {
    const y = hy + 40 + i * 40;
    const t = lineTotals(l);
    text(ctx, String(i + 1), cx[0], y, { font: `16px ${SANS}`, color: '#222' });
    text(ctx, l.desc + (l.custom && !l.customRate ? ' *' : ''), cx[1], y, { font: `16px ${SANS}`, color: '#222', maxW: 290 });
    text(ctx, l.shape || '', cx[2], y, { font: `15px ${SANS}`, color: '#222', maxW: 76 });
    text(ctx, `${t.boxes} ${unitOf(l) === 'doz' ? 'dz' : 'bx'}`, cx[3], y, { font: `16px ${SANS}`, color: '#222' });
    text(ctx, rateBreakdown(l), cx[4], y, { font: `14px ${SANS}`, color: '#333', maxW: cx[5] - cx[4] - 10 });
    const amt = t.missingRates.length && !t.amount ? '—' : inr(t.amount) + (t.missingRates.length ? '*' : '');
    text(ctx, amt, x + w, y, { font: `bold 16px ${SANS}`, color: '#222', align: 'right' });
    hline(ctx, x, x + w, y + 14, 0.6, '#c8cde0');
  });

  const ty = hy + 40 + lines.length * 40 + 12;
  hline(ctx, x, x + w, ty - 8, 1.6);
  text(ctx, `Total: ${totals.boxes} boxes`, x + 40, ty + 26, { font: `bold 18px ${SANS}` });
  text(ctx, `Order value ${inr(totals.amount)}`, x + w, ty + 26, { font: `bold 22px ${SANS}`, align: 'right' });
  text(ctx, '* style not in the NE price list - rate to be confirmed.   CD @ 2% on payment within 30 days.   Prices F.O.R. destination.',
    x, ty + 62, { font: `13px ${SANS}`, color: '#556', maxW: w });
}

export function canvasToJpeg(canvas, quality = 0.92) {
  return new Promise((res) => canvas.toBlob((b) => res(b), 'image/jpeg', quality));
}
