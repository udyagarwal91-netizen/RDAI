// Order model helpers shared by the UI, the form renderer and the tests.
import { CATALOG_BY_ID, rateFor } from './catalog.js';

export function emptyOrder() {
  const d = new Date();
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return {
    id: `O${Date.now().toString(36)}`,
    customer: { name: '', line2: '', line3: '', gstin: '' },
    orderNo: '',
    date: `${dd}/${mm}/${String(d.getFullYear()).slice(2)}`,
    transport: '',
    transcript: '',
    lines: [],
  };
}

export function lineRate(line, size) {
  if (line.productId) return rateFor(CATALOG_BY_ID[line.productId], size);
  return line.customRate || null;
}

export function lineTotals(line) {
  let boxes = 0;
  let amount = 0;
  const missingRates = [];
  for (const [s, q] of Object.entries(line.qty)) {
    const n = Number(q) || 0;
    if (!n) continue;
    boxes += n;
    const r = lineRate(line, Number(s));
    if (r == null) missingRates.push(Number(s));
    else amount += r * n;
  }
  return { boxes, amount, missingRates };
}

export function orderTotals(order) {
  let boxes = 0;
  let amount = 0;
  for (const l of order.lines) {
    const t = lineTotals(l);
    boxes += t.boxes;
    amount += t.amount;
  }
  return { boxes, amount };
}

export function unitOf(line) {
  const p = line.productId && CATALOG_BY_ID[line.productId];
  return p ? p.unit : 'box';
}

export const inr = (n) => `₹${Math.round(n).toLocaleString('en-IN')}`;
