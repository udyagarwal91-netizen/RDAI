import { CATALOG, CATALOG_BY_ID, ADULT_SIZES, KIDS_SIZES } from './catalog.js';
import { parseTranscript, setCustomProducts, newLineId } from './parser.js';
import { parseWithClaude, DEFAULT_MODEL } from './ai.js';
import { Listener, speechSupported } from './speech.js';
import { toRomanScript } from './hindi.js';
import { renderOrderForm, canvasToJpeg, ensureFonts } from './form-render.js';
import { emptyOrder, lineTotals, lineRate, orderTotals, unitOf, inr } from './order.js';

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Storage (per-device conveniences; everything still works without it)
// ---------------------------------------------------------------------------
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

let settings = { apiKey: '', model: DEFAULT_MODEL, custom: '', lang: 'hi-IN', prices: true, ...store.get('vob.settings', {}) };
// v2: most orders are spoken in Hindi, so Hindi became the default language.
if (!settings.v) { settings.lang = 'hi-IN'; settings.v = 2; store.set('vob.settings', settings); }
let order = store.get('vob.draft', null) || emptyOrder();
order.transcript = toRomanScript(order.transcript);
let manualEdits = false;

function parseCustomStyles(text) {
  return String(text || '').split('\n').map((row) => row.split('|').map((s) => s.trim()))
    .filter((c) => c[0]).map(([code, shape = '', rate = '']) => ({ code, shape, rate: Number(rate) || null }));
}
setCustomProducts(parseCustomStyles(settings.custom));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let toastTimer;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

const labelOf = (p) => `${p.short} ${p.shape}`.trim();
const LABEL_TO_ID = new Map(CATALOG.map((p) => [labelOf(p).toLowerCase(), p.id]));

function saveDraft() {
  store.set('vob.draft', order);
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------------------
// Customer fields
// ---------------------------------------------------------------------------
const FIELDS = [
  ['f-name', () => order.customer.name, (v) => { order.customer.name = v; }],
  ['f-line2', () => order.customer.line2, (v) => { order.customer.line2 = v; }],
  ['f-line3', () => order.customer.line3, (v) => { order.customer.line3 = v; }],
  ['f-gstin', () => order.customer.gstin, (v) => { order.customer.gstin = v; }],
  ['f-date', () => order.date, (v) => { order.date = v; }],
  ['f-orderno', () => order.orderNo, (v) => { order.orderNo = v; }],
  ['f-transport', () => order.transport, (v) => { order.transport = v; }],
];

function fillFields() {
  for (const [id, get] of FIELDS) $(id).value = get() || '';
  $('f-transcript').value = order.transcript || '';
}

for (const [id, , set] of FIELDS) {
  $(id).addEventListener('input', (e) => { set(e.target.value); saveDraft(); schedulePreview(); });
}

// ---------------------------------------------------------------------------
// Order lines
// ---------------------------------------------------------------------------
function sizesForLine(line) {
  const base = line.section === 'kids' ? KIDS_SIZES : ADULT_SIZES;
  const extra = Object.keys(line.qty).map(Number).filter((s) => !base.includes(s));
  return [...base, ...extra].sort((a, b) => a - b);
}

function lineHtml(line, idx) {
  const p = line.productId && CATALOG_BY_ID[line.productId];
  const t = lineTotals(line);
  const sizes = sizesForLine(line).map((s) => {
    const q = line.qty[s] || '';
    const hasRate = lineRate(line, s) != null;
    return `<label class="size ${q ? 'has' : ''} ${hasRate ? '' : 'norate'}">${s}
      <input inputmode="numeric" pattern="[0-9]*" data-size="${s}" value="${q}" aria-label="Size ${s} quantity"></label>`;
  }).join('');
  const unit = unitOf(line) === 'doz' ? 'dozen' : 'boxes';
  let price;
  if (p) {
    price = `<span>${t.boxes} ${unit} · <b>${inr(t.amount)}</b>${t.missingRates.length ? ` <span class="badge">no rate for ${t.missingRates.join(', ')}</span>` : ''}</span>`;
  } else {
    price = `<span class="rate-in"><span class="badge">not in price list</span> rate/box
      <input inputmode="numeric" data-field="customRate" value="${line.customRate || ''}" placeholder="₹"> · <b>${inr(t.amount)}</b></span>`;
  }
  return `<div class="line" data-id="${line.id}">
    <div class="line-top">
      <label class="desc">#${idx + 1} Product / style
        <input list="catalog-list" data-field="desc" value="${escapeHtml(p ? labelOf(p) : line.desc)}" autocomplete="off"></label>
      <label>Shape<input data-field="shape" value="${escapeHtml(line.shape)}"></label>
      <label>Section<select data-field="section">
        <option value="adult" ${line.section !== 'kids' ? 'selected' : ''}>Adult</option>
        <option value="kids" ${line.section === 'kids' ? 'selected' : ''}>Kids</option></select></label>
      <button class="del" data-del title="Remove line" aria-label="Remove line">×</button>
    </div>
    <div class="sizes">${sizes}</div>
    <div class="line-foot">${p ? `<span>${escapeHtml(p.name)}</span>` : '<span></span>'}${price}</div>
  </div>`;
}

function renderLines() {
  $('lines').innerHTML = order.lines.map(lineHtml).join('');
  $('lines-empty').hidden = order.lines.length > 0;
  updateSummary();
}

function updateSummary() {
  const t = orderTotals(order);
  $('order-summary').textContent = `${order.lines.length} items · ${t.boxes} boxes · ${inr(t.amount)}`;
}

function findLine(el) {
  const box = el.closest('.line');
  return box && order.lines.find((l) => l.id === box.dataset.id);
}

$('lines').addEventListener('input', (e) => {
  const line = findLine(e.target);
  if (!line) return;
  manualEdits = true;
  const el = e.target;
  if (el.dataset.size) {
    const v = parseInt(el.value, 10);
    if (v > 0) line.qty[el.dataset.size] = v; else delete line.qty[el.dataset.size];
    el.closest('.size').classList.toggle('has', v > 0);
    refreshLineFoot(line);
  } else if (el.dataset.field === 'shape') line.shape = el.value;
  else if (el.dataset.field === 'customRate') { line.customRate = Number(el.value) || null; refreshLineFoot(line); }
  saveDraft();
  updateSummary();
  schedulePreview();
});

$('lines').addEventListener('change', (e) => {
  const line = findLine(e.target);
  if (!line) return;
  const el = e.target;
  if (el.dataset.field === 'desc') {
    const id = LABEL_TO_ID.get(el.value.trim().toLowerCase());
    if (id) {
      const p = CATALOG_BY_ID[id];
      Object.assign(line, { productId: id, desc: p.short, shape: p.shape, custom: false });
      if (p.section === 'kids' && Object.keys(line.qty).every((s) => KIDS_SIZES.includes(Number(s)))) line.section = 'kids';
    } else {
      Object.assign(line, { productId: null, desc: el.value.trim(), custom: true });
    }
    renderLines();
  } else if (el.dataset.field === 'section') {
    line.section = el.value;
    renderLines();
  }
  manualEdits = true;
  saveDraft();
  schedulePreview();
});

$('lines').addEventListener('click', (e) => {
  if (!e.target.closest('[data-del]')) return;
  const line = findLine(e.target);
  order.lines = order.lines.filter((l) => l !== line);
  manualEdits = true;
  renderLines();
  saveDraft();
  schedulePreview();
});

function refreshLineFoot(line) {
  const box = document.querySelector(`.line[data-id="${line.id}"] .line-foot`);
  if (!box) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = lineHtml(line, 0);
  const foot = tmp.querySelector('.line-foot');
  // keep focus in the custom-rate input while typing
  const focused = document.activeElement && box.contains(document.activeElement);
  if (focused) {
    const b = box.querySelector('b');
    if (b) b.textContent = foot.querySelector('b').textContent;
  } else box.innerHTML = foot.innerHTML;
}

$('btn-add-line').addEventListener('click', () => {
  order.lines.push({ id: newLineId(), productId: null, desc: '', shape: '', section: 'adult', qty: {}, custom: true, customRate: null });
  manualEdits = true;
  renderLines();
  const inputs = $('lines').querySelectorAll('input[data-field="desc"]');
  inputs[inputs.length - 1]?.focus();
  saveDraft();
});

$('catalog-list').innerHTML = CATALOG.map((p) => `<option value="${escapeHtml(labelOf(p))}">${escapeHtml(p.name)}</option>`).join('');

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------
function applyResult(res, { silent } = {}) {
  const h = res.header || {};
  if (h.name && !order.customer.name) order.customer.name = h.name;
  if (h.place && !order.customer.line2) order.customer.line2 = h.place;
  if (h.transport && !order.transport) order.transport = h.transport;
  order.lines = res.lines;
  manualEdits = false;
  fillFields();
  renderLines();
  saveDraft();
  schedulePreview();

  const notes = [];
  if (res.warnings.length) notes.push(`<b>Please check:</b><ul>${res.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`);
  if (res.ignored.length) notes.push(`<b>Treated as conversation (not added):</b><ul>${res.ignored.slice(-6).map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`);
  $('parse-notes').innerHTML = notes.join('');
  $('parse-notes').hidden = !notes.length;
  if (!silent) toast(`${res.lines.length} item${res.lines.length === 1 ? '' : 's'} found`);
}

function runOffline({ silent } = {}) {
  const text = $('f-transcript').value;
  order.transcript = text;
  if (!text.trim()) { if (!silent) toast('Nothing to read yet - start listening or type the order'); return; }
  if (manualEdits && !silent && order.lines.length && !confirm('Rebuilding replaces the edits you made to the lines. Continue?')) return;
  if (manualEdits && silent) return;   // never overwrite hand edits while listening
  applyResult(parseTranscript(text), { silent });
}

$('btn-parse').addEventListener('click', () => runOffline());

$('btn-ai').addEventListener('click', async () => {
  const text = $('f-transcript').value;
  if (!text.trim()) { toast('Nothing to read yet'); return; }
  if (!settings.apiKey) { toast('Add your Anthropic API key in Settings first'); openSettings(); return; }
  if (manualEdits && order.lines.length && !confirm('Rebuilding replaces the edits you made to the lines. Continue?')) return;
  const btn = $('btn-ai');
  btn.disabled = true;
  btn.textContent = '✨ Reading…';
  try {
    order.transcript = text;
    applyResult(await parseWithClaude(text, { apiKey: settings.apiKey, model: settings.model }));
  } catch (err) {
    console.error(err);
    toast(err.message || 'AI parsing failed - using offline parser');
    runOffline();
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ Build with AI';
  }
});

$('btn-clear-talk').addEventListener('click', () => {
  if ($('f-transcript').value && !confirm('Clear the transcript?')) return;
  $('f-transcript').value = '';
  order.transcript = '';
  $('parse-notes').hidden = true;
  saveDraft();
});

$('f-transcript').addEventListener('input', (e) => { order.transcript = e.target.value; saveDraft(); });

// ---------------------------------------------------------------------------
// Voice
// ---------------------------------------------------------------------------
$('f-lang').value = settings.lang;
$('f-lang').addEventListener('change', (e) => {
  settings.lang = e.target.value;
  store.set('vob.settings', settings);
  if (listener && listener.active) { listener.stop(); startListening(); }
});

let listener = null;
function startListening() {
  listener = new Listener({
    lang: settings.lang,
    onFinal: (heard) => {
      // Hindi is recognised in Devanagari; show it in English letters.
      const txt = toRomanScript(heard);
      const ta = $('f-transcript');
      ta.value = (ta.value ? `${ta.value.replace(/\s+$/, '')}\n` : '') + txt;
      ta.scrollTop = ta.scrollHeight;
      order.transcript = ta.value;
      saveDraft();
      if ($('f-live').checked) runOffline({ silent: true });
    },
    onInterim: (txt) => { $('interim').textContent = toRomanScript(txt); },
    onState: (s) => {
      const on = s === 'listening';
      $('btn-mic').classList.toggle('on', on);
      $('btn-mic').setAttribute('aria-pressed', String(on));
      $('mic-label').textContent = on ? 'Stop' : 'Start listening';
      $('mic-status').textContent = on ? 'Listening… talk normally. Tap Stop when the order is done.' : 'Stopped. Check the lines below, then share the order form.';
    },
    onError: (msg) => toast(msg),
  });
  try { listener.start(); } catch (err) { toast(err.message); }
}

$('btn-mic').addEventListener('click', () => {
  if (listener && listener.active) {
    listener.stop();
    if ($('f-live').checked && !manualEdits) runOffline({ silent: true });
  } else startListening();
});

if (!speechSupported) {
  $('mic-status').textContent = 'This browser cannot convert speech to text. Open the app in Chrome (Android / Windows / Mac) — or type/paste the conversation below.';
  $('btn-mic').disabled = true;
}

// ---------------------------------------------------------------------------
// Preview & export
// ---------------------------------------------------------------------------
let previewTimer;
let lastCanvas = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(drawPreview, 250);
}

async function drawPreview() {
  await ensureFonts();
  lastCanvas = renderOrderForm(order, { withPrices: $('f-prices').checked });
  $('preview').src = lastCanvas.toDataURL('image/jpeg', 0.8);
}

$('f-prices').checked = settings.prices !== false;
$('f-prices').addEventListener('change', (e) => {
  settings.prices = e.target.checked;
  store.set('vob.settings', settings);
  schedulePreview();
});

function fileName() {
  const n = (order.customer.name || 'order').replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '');
  return `Order_${n}_${(order.date || '').replace(/\//g, '-')}.jpg`;
}

async function jpegFile() {
  await drawPreview();
  const blob = await canvasToJpeg(lastCanvas);
  return new File([blob], fileName(), { type: 'image/jpeg' });
}

async function download() {
  const file = await jpegFile();
  const url = URL.createObjectURL(file);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

$('btn-download').addEventListener('click', download);

$('btn-share').addEventListener('click', async () => {
  const file = await jpegFile();
  const t = orderTotals(order);
  const text = `Order – ${order.customer.name || ''} ${order.customer.line2 || ''} (${order.date}) – ${t.boxes} boxes, ${inr(t.amount)}`;
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: file.name, text }); } catch { /* user closed the share sheet */ }
  } else {
    await download();
    toast('Sharing is not supported here - JPEG downloaded instead');
  }
});

// ---------------------------------------------------------------------------
// Saved orders
// ---------------------------------------------------------------------------
function savedOrders() { return store.get('vob.orders', []); }

$('btn-save').addEventListener('click', () => {
  const list = savedOrders().filter((o) => o.id !== order.id);
  list.unshift({ ...order, savedAt: new Date().toISOString() });
  store.set('vob.orders', list.slice(0, 200));
  toast('Order saved on this phone');
});

$('btn-new').addEventListener('click', () => {
  if (order.lines.length && !confirm('Start a new order? Save this one first if you need it.')) return;
  listener?.active && listener.stop();
  order = emptyOrder();
  manualEdits = false;
  fillFields();
  renderLines();
  $('parse-notes').hidden = true;
  saveDraft();
  schedulePreview();
});

function renderOrdersList() {
  const list = savedOrders();
  $('orders-list').innerHTML = list.length ? list.map((o) => {
    const t = orderTotals(o);
    return `<div class="order-item"><div><b>${escapeHtml(o.customer.name || 'Unnamed')}</b> ${escapeHtml(o.customer.line2 || '')}
      <small>${escapeHtml(o.date)} · ${o.lines.length} items · ${t.boxes} boxes · ${inr(t.amount)}</small></div>
      <div><button class="btn small" data-open="${o.id}">Open</button> <button class="btn small ghost" data-remove="${o.id}">Delete</button></div></div>`;
  }).join('') : '<p class="empty">No saved orders yet.</p>';
}

$('btn-orders').addEventListener('click', () => { renderOrdersList(); $('dlg-orders').showModal(); });
$('orders-list').addEventListener('click', (e) => {
  const open = e.target.dataset.open;
  const rem = e.target.dataset.remove;
  if (open) {
    const o = savedOrders().find((x) => x.id === open);
    if (o) { order = structuredClone(o); delete order.savedAt; manualEdits = true; fillFields(); renderLines(); saveDraft(); schedulePreview(); }
    $('dlg-orders').close();
  } else if (rem && confirm('Delete this saved order?')) {
    store.set('vob.orders', savedOrders().filter((x) => x.id !== rem));
    renderOrdersList();
  }
});

// ---------------------------------------------------------------------------
// Price list
// ---------------------------------------------------------------------------
function renderPrices(q = '') {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = CATALOG.filter((p) => {
    const hay = `${p.name} ${p.short} ${p.shape} ${p.aliases.join(' ')}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  $('price-table').innerHTML = `<table><thead><tr><th>Product</th><th>Shape</th><th>Size @ rate</th><th>MRP</th></tr></thead><tbody>${
    rows.map((p) => `<tr><td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.shape)}</td><td class="r">${
      Object.entries(p.priceGroups).map(([k, r]) => `${k}: ₹${r}${p.unit === 'doz' ? '/dz' : ''}`).join('<br>')}</td><td>${p.mrp ?? ''}</td></tr>`).join('')
  }</tbody></table>`;
}
$('btn-prices').addEventListener('click', () => { renderPrices($('price-search').value); $('dlg-prices').showModal(); });
$('price-search').addEventListener('input', (e) => renderPrices(e.target.value));

document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
function openSettings() {
  $('s-key').value = settings.apiKey || '';
  $('s-model').value = settings.model || DEFAULT_MODEL;
  $('s-custom').value = settings.custom || '';
  $('dlg-settings').showModal();
}
$('btn-settings').addEventListener('click', openSettings);
$('dlg-settings').addEventListener('close', () => {
  if ($('dlg-settings').returnValue !== 'save') return;
  settings.apiKey = $('s-key').value.trim();
  settings.model = $('s-model').value.trim() || DEFAULT_MODEL;
  settings.custom = $('s-custom').value;
  store.set('vob.settings', settings);
  setCustomProducts(parseCustomStyles(settings.custom));
  // apply rates of known custom styles to current lines
  const known = parseCustomStyles(settings.custom);
  for (const l of order.lines) {
    if (!l.custom) continue;
    const k = known.find((c) => c.code.replace(/\s+/g, '').toLowerCase() === l.desc.replace(/\s+/g, '').toLowerCase());
    if (k && k.rate && !l.customRate) l.customRate = k.rate;
  }
  renderLines();
  schedulePreview();
  toast('Settings saved');
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
fillFields();
renderLines();
drawPreview();

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
