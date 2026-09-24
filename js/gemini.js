// Gemini turns a recorded shop visit into an order in two careful steps:
//
//   Step 1  LISTEN   - write down every turn of the conversation, numbered,
//                      in English letters. No judging, nothing left out.
//   Step 2  EXTRACT  - read that written conversation like an order clerk:
//                      a) the customer (name, town) wherever it was said,
//                      b) every product that came up anywhere,
//                      c) for each product: ORDERED, only ASKED about
//                         (colours, pack, rate, scheme) or CANCELLED,
//                      d) sizes and boxes for each ordered product, gathered
//                         from all the turns where it was discussed, with
//                         corrections applied.
//
// The app then merges duplicates and shows what was only asked about, so
// nothing is silently dropped. The API key is stored on the phone and
// requests go straight to Google.
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

function extras({ customStyles = [], corrections = [] }) {
  const custom = customStyles.length
    ? `\nThe rep's own styles (not in the price list) - spell them exactly like this:\n${customStyles.map((c) => `${c.code} | shape ${c.shape || '-'}`).join('\n')}\n`
    : '';
  const fixes = corrections.length
    ? `\nWords that often get misheard in this rep's visits (heard = meant):\n${corrections.map(([a, b]) => `${a} = ${b}`).join('\n')}\n`
    : '';
  return custom + fixes;
}

const SPEECH_NOTES = `- Hindi numbers: pachattar 75, assi 80, pachasi 85, nabbe 90, pachanve 95, sau 100, ek sau paanch 105, ek sau das 110, ek sau pandrah 115, ek sau bees 120, tees 30, paintees 35, chalis 40, paintalis 45, pachas 50, pachpan 55, saath 60, painsath 65, sattar 70, tihattar 73. Small numbers: ek 1, do 2, teen 3, char 4, paanch/pach 5, chhe 6, saat 7, aath 8, nau 9, das 10, gyarah 11, barah 12, pandrah 15, bees 20.
- Product words: RN = round neck vest, RNS = round neck with sleeves, O/E / I/E = outer / inner elastic, ICD / IWD / ICDP / RCD / IPD / OPD are drawer codes, "dabba/dabbe" = box(es).
- The phone may mishear brand names: "UP"/"rupee"/"Ravi" before a product word = Ruby; "late"/"light" = Lite; "easy" = Ezee; "marks"/"Marcus" = Marcos; "Sophia" = Sofiyaa; "gents" = Genteez.`;

export function buildTranscribeInstructions(opts = {}) {
  return `You are writing down, word for word, the audio of a Skipper Hosiery sales representative's visit to a retail shop in North-East India. The rep and the shopkeeper speak Hindi, Hinglish or Indian English.

Your only job is an exact, complete written record. Do NOT decide what the order is and do NOT leave anything out - greetings, family talk, questions about colours or pack sizes, rates, schemes, the shop name, everything goes in, in the order it was said.
- One entry per speaking turn, numbered from 1. Speaker "Rep" (the salesman), "Customer" (shopkeeper/staff) or "Other".
- English letters only (Roman Hinglish, never Devanagari). Keep the Hindi words as spoken ("pachasi mein do").
- Write numbers as digits when they are clearly numbers ("85 mein 2"), but never change or reorder what was said.
- Spell product names and codes the way the price list below spells them (Ruby ICD, Lite ICD, Ezee, Natkhat, Sofiyaa, Marcos...). Spell the shop name and town as best you can with proper capitals.
- If a word is unclear, write your best guess followed by (?). Never invent words that were not said.
${SPEECH_NOTES}
${extras(opts)}
Price list names (for spelling only):
${CATALOG.map((p) => p.name).join('; ')}`;
}

export function buildExtractInstructions(opts = {}) {
  return `You are the order clerk of a Skipper Hosiery sales representative. You get the numbered, written conversation of one whole shop visit. Real visits are messy: the customer orders two products, asks about a third (how many colours, how many pieces in a pack, rate, scheme), talks about family, orders the third product a few minutes later, orders a fourth and fifth in one go, asks about a sixth, and so on. The shop name can be said at any moment. Work through the WHOLE conversation carefully, in these steps:

STEP A - Customer. Find the shop/party name and its town anywhere in the conversation (start, middle or end, with or without "party ka naam"; the rep may just say "Sahak Cloth Store, Dibrugarh", the shopkeeper may say "likho, Manoj Textiles"). Also a transport/carrier if one is named. Note the turn numbers.

STEP B - Products. Go through every turn and list EVERY product or style that is mentioned anywhere, even once, even only in a question. One entry per distinct product: if the same product comes up in several places, it is still ONE entry and you collect all its turn numbers. "wahi / ditto / same" or a missing brand means the brand of the product just before ("Ruby IWD ... ICD pocket" = Ruby ICD pocket). A bare "ICD" means Ruby ICD. Match each to the price list id below, or leave product_id "" and write the spoken code in style_code for styles not in the list (e.g. "JFS 2409", "AHW 613", "SP 42"; a bare "2505" right after "JFS 2409" is "JFS 2505").

STEP C - Ordered or only asked about. For each product decide its status from ALL its turns:
- ORDERED: sizes and quantities were given for it (at any point), and not cancelled later.
- ASKED_ONLY: only questions or information - colours, pieces per pack, rate, MRP, scheme, stock, "dikhao" - and no quantities. Say briefly what was asked in asked_about.
- CANCELLED: ordered, then "hata do / cancel / nahi chahiye".
A product that was first asked about and later ordered is ORDERED.

STEP D - Sizes and boxes per ordered product. Collect the size -> boxes pairs for that product from every turn where it was ordered, and apply later corrections ("85 teen kar do" changes size 85 to 3). Quantities are boxes of 10 pcs (Sofiyaa and Honey: dozens).
- Both word orders happen: "pachasi mein do, nabbe mein teen" = size 85 -> 2, 90 -> 3; "saat pachasi mein, teen nabbe mein" = 7 boxes of 85, 3 of 90. Decide from the rhythm of the sentence.
- "85 se 100 tak do do dabbe" / "har size mein do" / "char char" = the same quantity in every size of that range; ranges skip 73 unless 73 is said.
- Adult sizes: 75 80 85 90 95 100 105 110 115 120 125 130. Kids sizes: 30 35 40 45 50 55 60 65 70 73 75 80 85.
- Numbers about schemes ("10 dabbe par 1 free"), pack size ("ek dabbe mein 10 piece"), colours ("5 colour") or rates ("880") are NOT order quantities.
- If a size or quantity is unclear, still give your best reading and explain in note. Never invent a quantity that was not said.

Also give, for each product, "heard": the exact words from the conversation that support your decision, and its turn numbers.
${SPEECH_NOTES}
${extras(opts)}
Price list (id | name | shape | unit | size-group@rate per box):
${catalogText()}`;
}

// Kept for tests/tools that want the whole rule set in one string.
export function buildInstructions(opts = {}) {
  return `${buildTranscribeInstructions(opts)}\n\n${buildExtractInstructions(opts)}`;
}

// Gemini response schemas (OpenAPI subset used by generationConfig.responseSchema)
const S = (type, extra = {}) => ({ type, ...extra });
export const TRANSCRIPT_SCHEMA = S('OBJECT', {
  properties: {
    turns: S('ARRAY', {
      items: S('OBJECT', {
        properties: {
          n: S('INTEGER'),
          speaker: S('STRING', { enum: ['Rep', 'Customer', 'Other'] }),
          text: S('STRING'),
        },
        required: ['n', 'speaker', 'text'],
      }),
    }),
  },
  required: ['turns'],
});

export const ORDER_SCHEMA = S('OBJECT', {
  properties: {
    customer: S('OBJECT', {
      properties: {
        name: S('STRING', { description: 'Shop / party name as said anywhere in the visit; empty only if never said' }),
        place: S('STRING', { description: 'Town of the shop' }),
        transport: S('STRING', { description: 'Transport / carrier if said' }),
        turns: S('ARRAY', { items: S('INTEGER') }),
      },
      required: ['name', 'place', 'transport', 'turns'],
    }),
    products: S('ARRAY', {
      description: 'Every product mentioned anywhere in the visit, one entry per product',
      items: S('OBJECT', {
        properties: {
          spoken_as: S('STRING', { description: 'How it was said, e.g. "Ezee colour RN"' }),
          product_id: S('STRING', { description: 'Price-list id, or "" if the style is not in the price list' }),
          style_code: S('STRING', { description: 'Spoken style code when product_id is ""' }),
          shape: S('STRING'),
          status: S('STRING', { enum: ['ORDERED', 'ASKED_ONLY', 'CANCELLED'] }),
          asked_about: S('STRING', { description: 'What was asked (colours, pack, rate...), empty if nothing' }),
          quantities: S('ARRAY', {
            items: S('OBJECT', { properties: { size: S('INTEGER'), qty: S('INTEGER') }, required: ['size', 'qty'] }),
          }),
          turns: S('ARRAY', { items: S('INTEGER') }),
          heard: S('STRING', { description: 'Exact words from the conversation behind this entry' }),
          note: S('STRING', { description: 'Doubts, empty if none' }),
        },
        required: ['spoken_as', 'product_id', 'style_code', 'shape', 'status', 'asked_about', 'quantities', 'turns', 'heard', 'note'],
      }),
    }),
    not_order: S('STRING', { description: 'One short sentence: what other talk there was (greetings, family, schemes...). The shop name/town are never part of this.' }),
  },
  required: ['customer', 'products', 'not_order'],
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

// One Gemini request (streamed, JSON answer), with retries and model fallback.
async function runGemini({ parts, system, schema, thinkingLevel, label }, ctx) {
  const { apiKey, model, onStatus, fetchImpl, sleep, state } = ctx;
  const say = (m) => onStatus?.(`${label}: ${m}`);
  const makeBody = () => JSON.stringify({
    system_instruction: { parts: [{ text: system }] },
    contents: [{ role: 'user', parts }],
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: schema,
      maxOutputTokens: 65536,
      // Stream thought summaries so the phone sees data flowing - iPhones drop
      // a request that stays silent for about a minute ("Load failed").
      ...(state.thinking ? { thinkingConfig: { thinkingLevel, includeThoughts: true } } : {}),
    },
  });

  const attempt = async (m) => {
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl && setTimeout(() => ctrl.abort(), 5 * 60 * 1000);
    try {
      const res = await call(`${API}/models/${encodeURIComponent(m)}:streamGenerateContent?alt=sse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
        body: makeBody(),
        signal: ctrl?.signal,
      }, fetchImpl, { sleep, onRetry: (n, of) => say(`Gemini is busy or the signal dropped – trying again (${n}/${of})…`) });
      return await readStream(res, say);
    } finally {
      if (timer) clearTimeout(timer);
    }
  };

  // The chosen (or last working) model first; if it stays busy, try the others.
  const chosen = state.model || model || DEFAULT_GEMINI_MODEL;
  const models = [chosen, ...FALLBACK_MODELS.filter((m) => m !== chosen)];
  let answer = null;
  let lastErr = null;
  for (const m of models) {
    if (m !== chosen) say(`Gemini is busy – trying ${m}…`);
    for (let tries = 0; tries < 2 && !answer; tries++) {
      try {
        answer = await attempt(m);
        state.model = m;
      } catch (err) {
        lastErr = err;
        // a model that does not take the thinking settings: ask again without
        if (err.status === 400 && state.thinking && /think/i.test(err.message)) { state.thinking = false; tries--; continue; }
        const fallbackable = BUSY.includes(err.status) || (m !== chosen && err.status === 404);
        if (!fallbackable) throw err;
        if (err.status !== 0) break;          // busy: next model; dropped connection: same model once more
        say('The connection dropped – trying again…');
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
  try { return JSON.parse(answer.text); } catch { throw new Error('Gemini gave an unreadable answer. Tap Analyze again.'); }
}

// "[4] Customer: Ruby ICD 85 mein do" <-> {n, speaker, text}
export function turnsToText(turns) {
  return turns.map((t) => `[${t.n}] ${t.speaker}: ${String(t.text).trim()}`).join('\n');
}
export function textToTurns(text) {
  return String(text || '').split('\n').map((l) => l.trim()).filter(Boolean).map((l, i) => {
    const m = l.match(/^(?:\[(\d+)\]\s*)?(?:(Rep|Customer|Other)\s*:\s*)?(.*)$/i);
    return { n: m[1] ? Number(m[1]) : i + 1, speaker: m[2] ? m[2][0].toUpperCase() + m[2].slice(1).toLowerCase() : 'Other', text: m[3] };
  });
}

/**
 * Analyse a recorded visit (audio Blob) - or, when no audio, a written conversation.
 * @returns {Promise<{transcript, header, lines, warnings, ignored, asked, model}>}
 */
export async function analyzeConversation({ audio, text }, { apiKey, model, customStyles, corrections, onStatus, fetchImpl = fetch, sleep = wait } = {}) {
  if (!apiKey) throw new Error('Add your Gemini API key in Settings first.');
  const opts = { customStyles, corrections };
  const ctx = { apiKey, model, onStatus, fetchImpl, sleep, state: { thinking: true, model: null } };

  // Step 1 - write down the whole conversation
  let turns;
  if (audio) {
    onStatus?.('Step 1 of 2: sending the recording…');
    const mime = geminiMime(audio.type);
    const media = audio.size <= INLINE_LIMIT
      ? { inline_data: { mime_type: mime, data: await toBase64(audio) } }
      : await uploadFile(audio, mime, apiKey, fetchImpl);
    const got = await runGemini({
      parts: [media, { text: 'Write down this whole shop visit, every turn, numbered.' }],
      system: buildTranscribeInstructions(opts),
      schema: TRANSCRIPT_SCHEMA,
      thinkingLevel: 'low',
      label: 'Step 1 of 2 – writing down the conversation',
    }, ctx);
    turns = (got.turns || []).filter((t) => t && String(t.text || '').trim());
    if (!turns.length) throw new Error('Gemini could not hear any conversation in this recording. Check the recording plays, then try again.');
  } else {
    turns = textToTurns(text);
  }
  const transcript = turnsToText(turns);

  // Step 2 - customer, products, ordered/asked, sizes per product
  const out = await runGemini({
    parts: [{ text: `The whole conversation of the visit, numbered:\n\n${transcript}\n\nWork through steps A to D and return the result.` }],
    system: buildExtractInstructions(opts),
    schema: ORDER_SCHEMA,
    thinkingLevel: 'medium',
    label: 'Step 2 of 2 – finding customer, products and sizes',
  }, ctx);

  return { ...toOrderResult(out), transcript, model: ctx.state.model };
}

// Read a streamGenerateContent (SSE) response: "data: {...}" events.
async function readStream(res, say) {
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
      if (p.thought) { thoughts++; say('thinking…'); continue; }
      if (p.text) { text += p.text; say(`writing… (${Math.round(text.length / 100) / 10}k)`); }
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

/**
 * Map Gemini's answer onto the app's order-line shape (same as parseTranscript).
 * Accepts the step-2 answer ({customer, products}) and the older {lines} form.
 */
export function toOrderResult(out) {
  const warnings = [];
  const asked = [];
  const lines = [];
  const entries = out.products || (out.lines || []).map((l) => ({ ...l, status: 'ORDERED' }));

  for (const e of entries) {
    const p = e.product_id && CATALOG_BY_ID[e.product_id];
    const desc = p ? p.short : (e.style_code || e.product_id || e.spoken_as || '?').toUpperCase();
    const label = `${desc}${(p ? p.shape : e.shape) ? ` ${p ? p.shape : e.shape}` : ''}`;
    const where = e.turns?.length ? ` (turn ${e.turns.join(', ')})` : '';
    if (e.status === 'ASKED_ONLY') { asked.push(`${label}${e.asked_about ? ` – ${e.asked_about}` : ''}${where}`); continue; }
    if (e.status === 'CANCELLED') { asked.push(`${label} – ordered, then cancelled${where}`); continue; }

    const qty = {};
    for (const { size, qty: q } of e.quantities || []) {
      if (!(q > 0)) continue;
      if (!(ADULT_SIZES.includes(size) || KIDS_SIZES.includes(size))) {
        warnings.push(`${desc}: size ${size} is not on the form – please check`);
        continue;
      }
      qty[size] = q;
    }
    if (!Object.keys(qty).length) {
      warnings.push(`${label}: ordered but no sizes/boxes were understood${where} – please fill in`);
    }
    if (e.product_id && !p) warnings.push(`${desc}: not found in the price list – check the product`);

    // one line per product: merge if Gemini listed the same product twice
    const key = p ? `${p.id}` : `c:${desc.replace(/\s+/g, '')}|${e.shape || ''}`;
    const existing = lines.find((l) => l._key === key);
    if (existing) {
      Object.assign(existing.qty, qty);
      existing.heard = [existing.heard, e.heard].filter(Boolean).join(' … ');
      existing.turns = [...new Set([...(existing.turns || []), ...(e.turns || [])])].sort((a, b) => a - b);
    } else {
      lines.push({
        _key: key,
        id: newLineId(),
        productId: p ? p.id : null,
        desc,
        shape: e.shape || (p ? p.shape : ''),
        section: 'adult',
        qty,
        custom: !p,
        customRate: null,
        heard: e.heard || '',
        turns: e.turns || [],
      });
    }
    if (e.note) warnings.push(`${desc}: ${e.note}`);
  }

  for (const l of lines) {
    const p = l.productId && CATALOG_BY_ID[l.productId];
    const sizes = Object.keys(l.qty).map(Number);
    const kids = (p && p.section === 'kids' && sizes.every((s) => KIDS_SIZES.includes(s))) || sizes.some((s) => s < 75 || s === 73);
    l.section = kids ? 'kids' : 'adult';
    delete l._key;
  }

  const c = out.customer || {};
  return {
    transcript: String(out.transcript || '').trim(),
    header: { name: c.name || undefined, place: c.place || undefined, transport: c.transport || undefined },
    lines,
    warnings,
    asked,
    ignored: out.not_order ? [out.not_order] : [],
  };
}
