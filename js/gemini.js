// Gemini listens to the whole recorded conversation and returns
//   1. a transcript in English letters (Roman Hinglish), and
//   2. only the order: product, sizes, quantities - with the small talk,
//      scheme / rate / family chat filtered out.
// The API key is stored on the phone and requests go straight to Google.
import { CATALOG, CATALOG_BY_ID, KIDS_SIZES, ADULT_SIZES, sizeGroups } from './catalog.js';
import { newLineId } from './parser.js';

export const DEFAULT_GEMINI_MODEL = 'gemini-3.8-flash';
const API = 'https://generativelanguage.googleapis.com/v1beta';
const INLINE_LIMIT = 14 * 1024 * 1024;   // request cap is 20 MB incl. base64 overhead

function catalogText() {
  return CATALOG.map((p) => {
    const sizes = sizeGroups(p).map(([k, r]) => `${k}@${r}`).join(' ');
    return `${p.id} | ${p.name} | shape ${p.shape || '-'} | ${p.unit} | ${sizes}`;
  }).join('\n');
}

export function buildInstructions({ customStyles = [], corrections = [] } = {}) {
  const custom = customStyles.length
    ? `\nThe rep's own styles (not in the price list) - use style_code exactly as written here:\n${customStyles.map((c) => `${c.code} | shape ${c.shape || '-'}`).join('\n')}\n`
    : '';
  const fixes = corrections.length
    ? `\nWords this rep says the phone/customer often gets wrong (heard = meant):\n${corrections.map(([a, b]) => `${a} = ${b}`).join('\n')}\n`
    : '';
  return `You are the order clerk for a Skipper Hosiery sales representative in North-East India.
You receive the audio of a whole visit to a retail shop: the rep and the shopkeeper talk in Hindi, Hinglish or Indian English. Most of the talk is NOT the order - greetings, family and health, schemes and offers, colour charts, rates, payment, gossip. Order lines are scattered in between.

Do two things:
1. transcript: write down the whole conversation in English letters (Roman Hinglish, never Devanagari), one line per turn, starting "Rep:" or "Customer:". Write product names and codes the way they are spelled in the price list below (Ruby IWD, Lite ICD, Natkhat, Sofiyaa...), and sizes/quantities as digits.
2. lines: only what was actually ordered, after all corrections.

How orders are spoken:
- A line is a product/style, then sizes with box quantities. Both orders happen: "Ruby IWD pachasi mein do, nabbe mein teen" (size 85 -> 2 boxes, 90 -> 3) and "saat pachasi mein, teen nabbe mein" (7 boxes of 85, 3 of 90). Decide from the rhythm of the whole sentence.
- Hindi numbers: pachattar 75, assi 80, pachasi 85, nabbe 90, pachanve 95, sau 100, ek sau paanch 105, ek sau das 110, tees 30, paintees 35, chalis 40, paintalis 45, pachas 50, pachpan 55, saath 60, painsath 65, sattar 70, tihattar 73.
- "85 se 100 tak do do dabbe" / "har size mein do" / "char char" = same quantity in every size of the range. Ranges skip 73 unless 73 is said.
- Adult sizes: 75 80 85 90 95 100 105 110 115 120 125 130. Kids sizes: 30 35 40 45 50 55 60 65 70 73 75 80 85.
- "wahi / ditto / same" = same brand or shape as the previous line ("Ruby IWD ... ICD pocket" = Ruby ICD pocket). A bare "ICD" means Ruby ICD.
- Corrections win: "85 teen kar do", "Lite brief hata do / cancel" - apply them; the final state is the order.
- Quantities are boxes of 10 pcs (Sofiyaa and Honey: dozens). "Dus dabbe par ek free" is a scheme, not an order. "Rate kya hai" is a question, not an order.
- Styles not in the price list (e.g. "JFS 2409", "AHW 613", "SP 42", "ADT 701", "F 46"): product_id "" and the code in style_code. A bare number like "2505" right after "JFS 2409" is "JFS 2505". Their shape is usually "Net" or "WSP (-15%)" (wholesale less a percentage); "same" repeats the previous shape.
- RN = round neck vest, RNS = round neck with sleeves, O/E / I/E = outer / inner elastic.
- customer: the shop / party name and its town are ALWAYS needed for the order form. They can come anywhere - start, middle or end - and often without any "party ka naam" phrase: the rep may just say "Sahak Cloth Store, Dibrugarh" or the shopkeeper may say "likho, Manoj Textiles". Put them in customer.name and customer.place (English letters, proper capitals). Never count the shop name or town as "not order" talk. Transport ("Assam Roadways se bhejna") goes in customer.transport.
- For every line, quote in "heard" the words from the conversation that gave you that line, and put anything you are unsure of in "note". Never invent a line you did not hear.
${custom}${fixes}
Price list (id | name | shape | unit | size-group@rate per box):
${catalogText()}`;
}

// Gemini response schema (OpenAPI subset used by generationConfig.responseSchema)
const S = (type, extra = {}) => ({ type, ...extra });
export const RESPONSE_SCHEMA = S('OBJECT', {
  properties: {
    transcript: S('STRING', { description: 'Whole conversation in English letters, one turn per line, "Rep:" / "Customer:"' }),
    customer: S('OBJECT', {
      properties: {
        name: S('STRING', { description: 'Shop / party name as said anywhere in the visit, e.g. "Sahak Cloth Store"; empty only if never said' }),
        place: S('STRING', { description: 'Town of the shop, e.g. "Dibrugarh"' }),
        transport: S('STRING', { description: 'Transport / carrier if said' }),
      },
      required: ['name', 'place', 'transport'],
    }),
    lines: S('ARRAY', {
      items: S('OBJECT', {
        properties: {
          product_id: S('STRING', { description: 'Price-list id, or "" if the style is not in the price list' }),
          style_code: S('STRING', { description: 'Spoken style code when product_id is ""' }),
          shape: S('STRING'),
          quantities: S('ARRAY', {
            items: S('OBJECT', { properties: { size: S('INTEGER'), qty: S('INTEGER') }, required: ['size', 'qty'] }),
          }),
          heard: S('STRING', { description: 'Words from the conversation this line came from' }),
          note: S('STRING', { description: 'Doubts, empty if none' }),
        },
        required: ['product_id', 'style_code', 'shape', 'quantities', 'heard', 'note'],
      }),
    }),
    not_order: S('STRING', { description: 'One short sentence: what non-order talk was left out (greetings, family, schemes, rates...). The shop name/town are NOT left out - they go in customer.' }),
  },
  required: ['transcript', 'customer', 'lines', 'not_order'],
});

// MediaRecorder / file types -> types Gemini accepts
export function geminiMime(type) {
  const t = String(type || '').split(';')[0].toLowerCase();
  const map = {
    'audio/webm': 'audio/webm', 'video/webm': 'audio/webm', 'audio/ogg': 'audio/ogg', 'audio/opus': 'audio/opus',
    'audio/mp4': 'audio/m4a', 'audio/x-m4a': 'audio/m4a', 'audio/m4a': 'audio/m4a', 'video/mp4': 'audio/m4a',
    'audio/aac': 'audio/aac', 'audio/mpeg': 'audio/mpeg', 'audio/mp3': 'audio/mp3', 'audio/wav': 'audio/wav',
    'audio/x-wav': 'audio/wav', 'audio/wave': 'audio/wav', 'audio/flac': 'audio/flac', 'audio/aiff': 'audio/aiff',
  };
  return map[t] || 'audio/webm';
}

async function toBase64(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = '';
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(bin);
}

// Other current Flash models to fall back to when the chosen one is
// overloaded (503) or rate-limited. Tried in this order.
export const FALLBACK_MODELS = ['gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash'];
// 0 = the connection itself failed ("Load failed" on iPhone, "Failed to fetch")
const BUSY = [0, 429, 500, 502, 503, 504];

class GeminiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function call(url, init, fetchImpl, { retries = 3, onRetry, sleep = wait } = {}) {
  let last;
  for (let attempt = 0; attempt <= retries; attempt++) {
    let res;
    try {
      res = await fetchImpl(url, init);
    } catch (err) {
      if (err?.name === 'AbortError') throw new GeminiError('Gemini took too long to answer. Tap “Analyze recording” again.', 0);
      res = { ok: false, status: 0, headers: null, json: async () => ({ error: { message: err?.message || 'connection failed' } }) };
    }
    if (res.ok) return res;
    last = res;
    if (!BUSY.includes(res.status) || attempt === retries) break;
    // Google asks to back off: 2s, 5s, 10s (or its Retry-After, max 20s)
    const after = Number(res.headers?.get?.('retry-after'));
    const ms = after > 0 ? Math.min(after * 1000, 20000) : [2000, 5000, 10000][attempt] || 10000;
    onRetry?.(attempt + 1, retries);
    await sleep(ms);
  }
  let detail = '';
  try { detail = (await last.json()).error?.message || ''; } catch { /* not json */ }
  const st = last.status;
  if (st === 400 && /api key/i.test(detail)) throw new GeminiError('Gemini says the API key is not valid. Check it in Settings.', st);
  if (st === 403) throw new GeminiError('Gemini refused the API key (403). Check the key and that billing is on.', st);
  if (st === 404) throw new GeminiError(`Gemini model not found. Change the model name in Settings. (${detail})`, st);
  if (st === 429) throw new GeminiError('Gemini quota exceeded (429). Check your Gemini balance / limits, then tap Analyze again.', st);
  if (st === 0) throw new GeminiError(`The connection to Gemini dropped (${detail}).`, 0);
  throw new GeminiError(`Gemini error ${st}: ${detail || 'please try again'}`, st);
}

// Large recordings go through the Gemini Files API instead of inline data.
async function uploadFile(blob, mime, apiKey, fetchImpl) {
  const start = await call(`${API.replace('/v1beta', '/upload/v1beta')}/files`, {
    method: 'POST',
    headers: {
      'x-goog-api-key': apiKey,
      'X-Goog-Upload-Protocol': 'resumable',
      'X-Goog-Upload-Command': 'start',
      'X-Goog-Upload-Header-Content-Length': String(blob.size),
      'X-Goog-Upload-Header-Content-Type': mime,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ file: { display_name: `visit-${Date.now()}` } }),
  }, fetchImpl);
  const uploadUrl = start.headers.get('x-goog-upload-url');
  if (!uploadUrl) throw new Error('Recording is too long to send in one go. Split the visit into two recordings.');
  const done = await call(uploadUrl, {
    method: 'POST',
    headers: { 'X-Goog-Upload-Offset': '0', 'X-Goog-Upload-Command': 'upload, finalize', 'Content-Length': String(blob.size) },
    body: blob,
  }, fetchImpl);
  let file = (await done.json()).file;
  for (let i = 0; i < 60 && file.state === 'PROCESSING'; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    file = await (await call(`${API}/${file.name}`, { headers: { 'x-goog-api-key': apiKey } }, fetchImpl)).json();
  }
  if (file.state === 'FAILED') throw new Error('Gemini could not read this audio file.');
  return { file_data: { mime_type: mime, file_uri: file.uri } };
}

/**
 * Analyse a recorded visit (audio Blob) - or, when no audio, a typed transcript.
 * @returns {Promise<{transcript: string, header: object, lines: Array, warnings: string[], ignored: string[]}>}
 */
export async function analyzeConversation({ audio, text }, { apiKey, model, customStyles, corrections, onStatus, fetchImpl = fetch, sleep = wait } = {}) {
  if (!apiKey) throw new Error('Add your Gemini API key in Settings first.');
  const parts = [];
  if (audio) {
    const mime = geminiMime(audio.type);
    parts.push(audio.size <= INLINE_LIMIT
      ? { inline_data: { mime_type: mime, data: await toBase64(audio) } }
      : await uploadFile(audio, mime, apiKey, fetchImpl));
    parts.push({ text: 'This is the recording of the whole shop visit. Listen to all of it, then return the transcript and the order.' });
  } else {
    parts.push({ text: `This is the transcript of the whole shop visit (from speech-to-text, so expect mis-heard words):\n\n${text}\n\nReturn a cleaned transcript and the order.` });
  }

  const makeBody = (thinking) => JSON.stringify({
    system_instruction: { parts: [{ text: buildInstructions({ customStyles, corrections }) }] },
    contents: [{ role: 'user', parts }],
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: RESPONSE_SCHEMA,
      maxOutputTokens: 32768,
      // Picking order lines needs little reasoning: think lightly (faster), and
      // stream thought summaries so the phone sees data flowing - iPhones drop
      // a request that stays silent for about a minute ("Load failed").
      ...(thinking ? { thinkingConfig: { thinkingLevel: 'low', includeThoughts: true } } : {}),
    },
  });
  let thinking = true;

  // One try: stream the answer, collecting the JSON text parts.
  const attempt = async (m) => {
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl && setTimeout(() => ctrl.abort(), 5 * 60 * 1000);
    try {
      const res = await call(`${API}/models/${encodeURIComponent(m)}:streamGenerateContent?alt=sse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
        body: makeBody(thinking),
        signal: ctrl?.signal,
      }, fetchImpl, { sleep, onRetry: (n, of) => onStatus?.(`Gemini is busy or the signal dropped – trying again (${n}/${of})…`) });
      return await readStream(res, onStatus);
    } finally {
      if (timer) clearTimeout(timer);
    }
  };

  // The chosen model first; if it stays busy/unreachable, try the others.
  const chosen = model || DEFAULT_GEMINI_MODEL;
  const models = [chosen, ...FALLBACK_MODELS.filter((m) => m !== chosen)];
  let answer = null;
  let usedModel = chosen;
  let lastErr = null;
  for (const m of models) {
    if (m !== chosen) onStatus?.(`Gemini is busy – trying ${m}…`);
    for (let tries = 0; tries < 2 && !answer; tries++) {
      try {
        answer = await attempt(m);
        usedModel = m;
      } catch (err) {
        lastErr = err;
        // older/newer models may not take the thinking settings: retry without
        if (err.status === 400 && thinking && /think/i.test(err.message)) { thinking = false; tries--; continue; }
        const fallbackable = BUSY.includes(err.status) || (m !== chosen && err.status === 404);
        if (!fallbackable) throw err;
        if (err.status !== 0) break;          // busy: next model; dropped connection: same model once more
        onStatus?.('The connection dropped – trying again…');
        await sleep(2000);
      }
    }
    if (answer) break;
  }
  if (!answer) {
    if (lastErr && lastErr.status === 429) throw lastErr;
    if (lastErr && lastErr.status === 0) {
      throw new GeminiError('Could not reach Gemini – the connection kept dropping (weak signal, or the app went to the background). '
        + 'Your recording is saved. Keep this screen open and tap “Analyze recording” again.', 0);
    }
    throw new GeminiError('Google’s Gemini servers are overloaded right now (not a problem with your key or the app). '
      + 'Your recording is saved – tap “Analyze recording” again in a few minutes.', lastErr?.status);
  }

  if (answer.blockReason) throw new Error(`Gemini blocked the request (${answer.blockReason}).`);
  if (answer.finishReason === 'MAX_TOKENS') throw new Error('The visit was too long for one answer. Split it into two recordings.');
  let out;
  try { out = JSON.parse(answer.text); } catch { throw new Error('Gemini gave an unreadable answer. Tap Analyze again.'); }
  return { ...toOrderResult(out), model: usedModel };
}

// Read a streamGenerateContent (SSE) response: "data: {...}" events.
async function readStream(res, onStatus) {
  let text = '';
  let finishReason = null;
  let blockReason = null;
  let thoughts = 0;
  const handle = (json) => {
    if (json.promptFeedback?.blockReason) blockReason = json.promptFeedback.blockReason;
    const cand = json.candidates?.[0];
    if (!cand) return;
    if (cand.finishReason) finishReason = cand.finishReason;
    for (const p of cand.content?.parts || []) {
      if (p.thought) { thoughts++; onStatus?.('Gemini is listening and thinking…'); continue; }
      if (p.text) { text += p.text; onStatus?.(`Gemini is writing the order… (${Math.round(text.length / 100) / 10}k)`); }
    }
  };
  let buf = '';
  const flushEvents = (final) => {
    const events = buf.split(/\r?\n\r?\n/);
    buf = final ? '' : events.pop();
    for (const ev of events) {
      const data = ev.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim()).join('');
      if (!data || data === '[DONE]') continue;
      try { handle(JSON.parse(data)); } catch { /* partial or keep-alive */ }
    }
  };
  try {
    if (res.body && res.body.getReader) {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        flushEvents(false);
      }
      flushEvents(true);
    } else {
      buf = await res.text();
      // a non-streamed JSON array/object is also accepted
      if (/^\s*[[{]/.test(buf)) { const j = JSON.parse(buf); (Array.isArray(j) ? j : [j]).forEach(handle); buf = ''; } else flushEvents(true);
    }
  } catch (err) {
    throw new GeminiError(`The connection to Gemini dropped (${err?.message || 'read failed'}).`, 0);
  }
  if (!text && !blockReason) throw new GeminiError('Gemini returned no answer.', 0);
  return { text, finishReason, blockReason, thoughts };
}

/** Map Gemini's JSON onto the app's order-line shape (same as parseTranscript). */
export function toOrderResult(out) {
  const warnings = [];
  const lines = [];
  for (const l of out.lines || []) {
    const p = l.product_id && CATALOG_BY_ID[l.product_id];
    const qty = {};
    for (const { size, qty: q } of l.quantities || []) {
      if (!(q > 0)) continue;
      const valid = ADULT_SIZES.includes(size) || KIDS_SIZES.includes(size);
      if (!valid) { warnings.push(`${p ? p.short : l.style_code}: size ${size} is not on the form - please check`); continue; }
      qty[size] = (qty[size] || 0) + q;
    }
    if (!Object.keys(qty).length) continue;
    const sizes = Object.keys(qty).map(Number);
    const kids = (p && p.section === 'kids' && sizes.every((s) => KIDS_SIZES.includes(s))) || sizes.some((s) => s < 75 || s === 73);
    const desc = p ? p.short : (l.style_code || l.product_id || '?').toUpperCase();
    if (l.product_id && !p) warnings.push(`${desc}: not found in the price list - check the product`);
    lines.push({
      id: newLineId(),
      productId: p ? p.id : null,
      desc,
      shape: l.shape || (p ? p.shape : ''),
      section: kids ? 'kids' : 'adult',
      qty,
      custom: !p,
      customRate: null,
      heard: l.heard || '',
    });
    if (l.note) warnings.push(`${desc}: ${l.note}`);
  }
  const c = out.customer || {};
  return {
    transcript: String(out.transcript || '').trim(),
    header: { name: c.name || undefined, place: c.place || undefined, transport: c.transport || undefined },
    lines,
    warnings,
    ignored: out.not_order ? [out.not_order] : [],
  };
}
