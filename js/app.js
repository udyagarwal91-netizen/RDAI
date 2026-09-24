import { CATALOG, CATALOG_BY_ID, ADULT_SIZES, KIDS_SIZES, sizeGroups } from './catalog.js';
import { parseTranscript, setCustomProducts, newLineId, setCorrections } from './parser.js';
import { analyzeConversation, DEFAULT_GEMINI_MODEL } from './gemini.js';
import { Recorder, recordingSupported, formatDuration, saveRecording, loadRecording } from './recorder.js';
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

let settings = { geminiKey: '', geminiModel: DEFAULT_GEMINI_MODEL, custom: '', fixes: '', prices: true, ...store.get('vob.settings', {}) };
// v3: recordings are analysed by Gemini (the old live speech + Claude settings are gone)
if ((settings.v || 0) < 3) {
  delete settings.apiKey; delete settings.model; delete settings.lang;
  settings.v = 3;
  store.set('vob.settings', settings);
}
let order = store.get('vob.draft', null) || emptyOrder();
// "heard = correct" lines from Settings, e.g. "up icd = ruby icd"
function parseFixes(text) {
  return String(text || '').split('\n').map((row) => row.split('=').map((s) => s.trim())).filter((r) => r[0] && r[1]);
}
setCorrections(parseFixes(settings.fixes));
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
    ${line.heard ? `<span class="heard">Heard: “${escapeHtml(line.heard)}”</span>` : ''}
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
// Results
// ---------------------------------------------------------------------------
function knownStyleRates(lines) {
  const known = parseCustomStyles(settings.custom);
  for (const l of lines) {
    if (!l.custom) continue;
    const k = known.find((c) => c.code.replace(/\s+/g, '').toLowerCase() === l.desc.replace(/\s+/g, '').toLowerCase());
    if (k && k.rate && !l.customRate) l.customRate = k.rate;
  }
}

function applyResult(res) {
  const h = res.header || {};
  if (h.name && !order.customer.name) order.customer.name = h.name;
  if (h.place && !order.customer.line2) order.customer.line2 = h.place;
  if (h.transport && !order.transport) order.transport = h.transport;
  if (res.transcript) order.transcript = res.transcript;
  knownStyleRates(res.lines);
  order.lines = res.lines;
  manualEdits = false;
  fillFields();
  renderLines();
  saveDraft();
  schedulePreview();

  const notes = [];
  if (res.warnings.length) notes.push(`<b>Please check:</b><ul>${res.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`);
  if (res.ignored.length) notes.push(`<b>Left out (not order talk):</b><ul>${res.ignored.slice(-6).map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`);
  $('parse-notes').innerHTML = notes.join('');
  $('parse-notes').hidden = !notes.length;
  toast(`${res.lines.length} item${res.lines.length === 1 ? '' : 's'} found`);
  if (res.lines.length) $('order-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function geminiOptions() {
  return {
    apiKey: settings.geminiKey,
    model: settings.geminiModel,
    customStyles: parseCustomStyles(settings.custom),
    corrections: parseFixes(settings.fixes),
  };
}

function busy(on, text) {
  $('analyzing').hidden = !on;
  if (text) $('analyzing-text').textContent = text;
  for (const id of ['btn-analyze', 'btn-reanalyze-text', 'btn-rec']) $(id).disabled = on;
}

async function analyze({ audio, text }) {
  if (!settings.geminiKey) { toast('Add your Gemini API key in Settings first'); openSettings(); return; }
  if (manualEdits && order.lines.length && !confirm('This replaces the changes you made to the order lines. Continue?')) return;
  busy(true, audio ? 'Gemini is listening to the whole conversation… (about 10–40 seconds)' : 'Gemini is reading the conversation…');
  try {
    const res = await analyzeConversation({ audio, text }, { ...geminiOptions(), onStatus: (msg) => busy(true, msg) });
    applyResult(res);
    if (res.model !== settings.geminiModel) toast(`Done using ${res.model} (${settings.geminiModel} was busy)`);
  } catch (err) {
    console.error(err);
    const offline = !navigator.onLine ? ' You look offline – the recording is saved, tap Analyze when you have signal.' : '';
    $('parse-notes').innerHTML = `<b>Could not analyze:</b> ${escapeHtml(err.message || String(err))}${offline}`;
    $('parse-notes').hidden = false;
    toast('Analysis failed – your recording is safe, try again');
  } finally {
    busy(false);
  }
}

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------
let recording = null;   // Blob of the last visit
let recorder = null;

function showRecording(blob) {
  recording = blob;
  $('rec-box').hidden = !blob;
  if (!blob) return;
  const old = $('rec-player').src;
  $('rec-player').src = URL.createObjectURL(blob);
  if (old) URL.revokeObjectURL(old);
}

function recUi(on) {
  $('btn-rec').classList.toggle('on', on);
  $('btn-rec').setAttribute('aria-pressed', String(on));
  $('rec-label').textContent = on ? 'Stop & analyze' : 'Start recording';
  $('rec-time').hidden = !on;
  $('rec-box').hidden = on || !recording;
  document.querySelector('.file-btn').hidden = on;
  $('rec-status').textContent = on
    ? 'Recording… keep this screen open and the phone on the counter. Talk normally.'
    : 'Tap when you enter the shop and talk normally – Hindi, Hinglish or English. Tap again when you leave: Gemini listens to the whole conversation, leaves out the small talk and builds the order.';
}

$('btn-rec').addEventListener('click', async () => {
  if (recorder && recorder.active) {
    const blob = await recorder.stop();
    recUi(false);
    if (!blob || !blob.size) { toast('Nothing was recorded'); return; }
    showRecording(blob);
    await saveRecording(blob);
    analyze({ audio: blob });
    return;
  }
  if (!settings.geminiKey) { toast('Add your Gemini API key in Settings first'); openSettings(); return; }
  if (recording && !confirm('Start a new recording? The previous recording will be replaced (download it first with “Save audio” if you need it).')) return;
  recorder = new Recorder({ onTick: (ms) => { $('rec-time').textContent = formatDuration(ms); } });
  try {
    await recorder.start();
    recUi(true);
  } catch (err) {
    toast(err.name === 'NotAllowedError' ? 'Allow the microphone for this site, then try again' : (err.message || 'Could not start recording'));
  }
});

$('btn-analyze').addEventListener('click', () => { if (recording) analyze({ audio: recording }); });

$('f-audio').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  showRecording(file);
  await saveRecording(file);
  analyze({ audio: file });
});

$('btn-save-audio').addEventListener('click', () => {
  if (!recording) return;
  const ext = (recording.type.split('/')[1] || 'webm').split(';')[0].replace('mpeg', 'mp3').replace('x-m4a', 'm4a');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(recording);
  a.download = `Visit_${(order.customer.name || 'shop').replace(/[^a-z0-9]+/gi, '_')}_${(order.date || '').replace(/\//g, '-')}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
});

if (!recordingSupported) {
  $('rec-status').textContent = 'This browser cannot record audio. Open the app in Chrome or Safari – or pick a recording made with the phone’s voice recorder.';
  $('btn-rec').disabled = true;
}

// ---------------------------------------------------------------------------
// Conversation text (review, correct, rebuild)
// ---------------------------------------------------------------------------
$('btn-reanalyze-text').addEventListener('click', () => {
  const text = $('f-transcript').value;
  if (!text.trim()) { toast('No conversation text yet'); return; }
  order.transcript = text;
  if (settings.geminiKey) { analyze({ text }); return; }
  // no key: the built-in (offline) reader
  if (manualEdits && order.lines.length && !confirm('This replaces the changes you made to the order lines. Continue?')) return;
  applyResult(parseTranscript(text));
});

$('btn-copy-talk').addEventListener('click', async () => {
  const text = $('f-transcript').value;
  if (!text.trim()) { toast('Nothing to copy yet'); return; }
  try {
    await navigator.clipboard.writeText(text);
    toast('Conversation copied');
  } catch {
    $('f-transcript').select();
    toast('Text selected – tap Copy');
  }
});

$('btn-clear-talk').addEventListener('click', () => {
  if ($('f-transcript').value && !confirm('Clear the conversation text?')) return;
  $('f-transcript').value = '';
  order.transcript = '';
  $('parse-notes').hidden = true;
  saveDraft();
});

$('f-transcript').addEventListener('input', (e) => { order.transcript = e.target.value; saveDraft(); });

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
  if (recorder && recorder.active) { toast('Stop the recording first'); return; }
  order = emptyOrder();
  showRecording(null);
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
      sizeGroups(p).map(([k, r]) => `${k}: ₹${r}${p.unit === 'doz' ? '/dz' : ''}`).join('<br>')}</td><td>${p.mrp ?? ''}</td></tr>`).join('')
  }</tbody></table>`;
}
$('btn-prices').addEventListener('click', () => { renderPrices($('price-search').value); $('dlg-prices').showModal(); });
$('price-search').addEventListener('input', (e) => renderPrices(e.target.value));

document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
function openSettings() {
  $('s-key').value = settings.geminiKey || '';
  $('s-model').value = settings.geminiModel || DEFAULT_GEMINI_MODEL;
  $('s-custom').value = settings.custom || '';
  $('s-fixes').value = settings.fixes || '';
  $('dlg-settings').showModal();
}
$('btn-settings').addEventListener('click', openSettings);
$('dlg-settings').addEventListener('close', () => {
  if ($('dlg-settings').returnValue !== 'save') return;
  settings.geminiKey = $('s-key').value.trim();
  settings.geminiModel = $('s-model').value.trim() || DEFAULT_GEMINI_MODEL;
  settings.custom = $('s-custom').value;
  settings.fixes = $('s-fixes').value;
  setCorrections(parseFixes(settings.fixes));
  store.set('vob.settings', settings);
  setCustomProducts(parseCustomStyles(settings.custom));
  knownStyleRates(order.lines);
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
// bring back the last recording (e.g. after a reload with no signal)
loadRecording().then((r) => { if (r && r.blob && !recording) showRecording(r.blob); });
if (!settings.geminiKey) $('rec-status').textContent = 'First, add your Gemini API key: tap ⚙ Settings (top right). Then tap Start recording when you enter a shop.';

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
