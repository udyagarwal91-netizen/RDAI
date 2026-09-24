// Gemini listens to the whole recorded conversation and returns
//   1. a transcript in English letters (Roman Hinglish), and
//   2. only the order: product, sizes, quantities - with the small talk,
//      scheme / rate / family chat filtered out.
// The API key is stored on the phone and requests go straight to Google.
import { CATALOG, CATALOG_BY_ID, KIDS_SIZES, ADULT_SIZES } from './catalog.js';
import { newLineId } from './parser.js';

export const DEFAULT_GEMINI_MODEL = 'gemini-3.8-flash';
const API = 'https://generativelanguage.googleapis.com/v1beta';
const INLINE_LIMIT = 14 * 1024 * 1024;   // request cap is 20 MB incl. base64 overhead

function catalogText() {
  return CATALOG.map((p) => {
    const sizes = Object.entries(p.priceGroups).map(([k, r]) => `${k}@${r}`).join(' ');
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
- The customer/shop name and town are often said at the start ("party ka naam ... , Sibsagar se"). Transport ("Assam Roadways se bhejna") goes in transport.
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
      properties: { name: S('STRING'), place: S('STRING'), transport: S('STRING') },
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
    not_order: S('STRING', { description: 'One short sentence: what non-order talk was left out' }),
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

async function call(url, init, fetchImpl) {
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetchImpl(url, init);
    if (res.ok) return res;
    last = res;
    if (![429, 500, 502, 503, 504].includes(res.status)) break;
    await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
  }
  let detail = '';
  try { detail = (await last.json()).error?.message || ''; } catch { /* not json */ }
  if (last.status === 400 && /api key/i.test(detail)) throw new Error('Gemini says the API key is not valid. Check it in Settings.');
  if (last.status === 403) throw new Error('Gemini refused the API key (403). Check the key and that billing is on.');
  if (last.status === 404) throw new Error(`Gemini model not found. Change the model name in Settings. (${detail})`);
  if (last.status === 429) throw new Error('Gemini quota exceeded (429). Check your Gemini balance / limits, then tap Analyze again.');
  throw new Error(`Gemini error ${last.status}: ${detail || 'please try again'}`);
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
export async function analyzeConversation({ audio, text }, { apiKey, model, customStyles, corrections, fetchImpl = fetch } = {}) {
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

  const res = await call(`${API}/models/${encodeURIComponent(model || DEFAULT_GEMINI_MODEL)}:generateContent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
    body: JSON.stringify({
      system_instruction: { parts: [{ text: buildInstructions({ customStyles, corrections }) }] },
      contents: [{ role: 'user', parts }],
      generationConfig: { responseMimeType: 'application/json', responseSchema: RESPONSE_SCHEMA, maxOutputTokens: 32768 },
    }),
  }, fetchImpl);

  const data = await res.json();
  if (data.promptFeedback?.blockReason) throw new Error(`Gemini blocked the request (${data.promptFeedback.blockReason}).`);
  const cand = data.candidates?.[0];
  if (!cand) throw new Error('Gemini returned no answer. Tap Analyze again.');
  if (cand.finishReason === 'MAX_TOKENS') throw new Error('The visit was too long for one answer. Split it into two recordings.');
  const raw = (cand.content?.parts || []).filter((p) => !p.thought).map((p) => p.text || '').join('');
  let out;
  try { out = JSON.parse(raw); } catch { throw new Error('Gemini gave an unreadable answer. Tap Analyze again.'); }
  return toOrderResult(out);
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
