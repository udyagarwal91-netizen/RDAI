// Gemini two-step analysis (listen -> extract), with a fake Google server.
// These tests check what the app sends and how it handles the answers; how
// well Gemini itself understands a visit can only be judged on real recordings.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  analyzeConversation, toOrderResult, geminiMime, buildTranscribeInstructions, buildExtractInstructions,
  turnsToText, textToTurns,
} from '../js/gemini.js';
import { lineTotals } from '../js/order.js';

// A messy visit: 2 products ordered, the 3rd asked about (colours / pack) and
// ordered later, the shop name in the middle, family talk, a 6th product only
// asked about, a correction and a cancellation.
const TURNS = [
  { n: 1, speaker: 'Rep', text: 'Namaste bhai sahab, kaise ho?' },
  { n: 2, speaker: 'Customer', text: 'Badhiya. Ruby ICD 85 mein 5, 90 mein 10.' },
  { n: 3, speaker: 'Customer', text: 'Ezee colour RN 85 se 100 tak do do dabbe.' },
  { n: 4, speaker: 'Customer', text: 'Lite ICD mein kitne colour hai? Ek dabbe mein kitne piece?' },
  { n: 5, speaker: 'Rep', text: '5 colour, ek dabbe mein 10 piece.' },
  { n: 6, speaker: 'Rep', text: 'Aur papa ki tabiyat kaisi hai?' },
  { n: 7, speaker: 'Rep', text: 'Sahak Cloth Store, Dibrugarh likh raha hoon.' },
  { n: 8, speaker: 'Customer', text: 'Theek hai. Lite ICD 85 aur 90 mein paanch paanch.' },
  { n: 9, speaker: 'Customer', text: 'Marcos colour brief 85 2 90 3, Natkhat print vest 60 65 70 char char, Lite brief 85 2.' },
  { n: 10, speaker: 'Customer', text: 'Classic gym vest ka rate kya hai? Scheme hai? 10 dabbe par 1 free.' },
  { n: 11, speaker: 'Customer', text: 'Ruby ICD 85 teen kar do. Lite brief hata do.' },
];

const ORDER = {
  customer: { name: 'Sahak Cloth Store', place: 'Dibrugarh', transport: '', turns: [7] },
  products: [
    { spoken_as: 'Ruby ICD', product_id: 'ruby-icd', style_code: '', shape: 'O/E', status: 'ORDERED', asked_about: '', quantities: [{ size: 85, qty: 3 }, { size: 90, qty: 10 }], turns: [2, 11], heard: 'Ruby ICD 85 mein 5, 90 mein 10 … 85 teen kar do', note: '' },
    { spoken_as: 'Ezee colour RN', product_id: 'ezee-cf-rn', style_code: '', shape: 'RN', status: 'ORDERED', asked_about: '', quantities: [85, 90, 95, 100].map((size) => ({ size, qty: 2 })), turns: [3], heard: 'Ezee colour RN 85 se 100 tak do do dabbe', note: '' },
    { spoken_as: 'Lite ICD', product_id: 'lite-icd', style_code: '', shape: 'O/E', status: 'ORDERED', asked_about: 'colours, pieces per box', quantities: [{ size: 85, qty: 5 }, { size: 90, qty: 5 }], turns: [4, 8], heard: 'Lite ICD 85 aur 90 mein paanch paanch', note: '' },
    { spoken_as: 'Marcos colour brief', product_id: 'marcos-cbrief', style_code: '', shape: 'O/E', status: 'ORDERED', asked_about: '', quantities: [{ size: 85, qty: 2 }, { size: 90, qty: 3 }], turns: [9], heard: 'Marcos colour brief 85 2 90 3', note: '' },
    { spoken_as: 'Natkhat print vest', product_id: 'natkhat-rn', style_code: '', shape: 'RN', status: 'ORDERED', asked_about: '', quantities: [60, 65, 70].map((size) => ({ size, qty: 4 })), turns: [9], heard: 'Natkhat print vest 60 65 70 char char', note: '' },
    { spoken_as: 'Lite brief', product_id: 'lite-iebrief', style_code: '', shape: 'I/E', status: 'CANCELLED', asked_about: '', quantities: [], turns: [9, 11], heard: 'Lite brief hata do', note: '' },
    { spoken_as: 'Classic gym vest', product_id: 'classic-gym', style_code: '', shape: '', status: 'ASKED_ONLY', asked_about: 'rate and scheme', quantities: [], turns: [10], heard: 'Classic gym vest ka rate kya hai', note: '' },
  ],
  not_order: 'Greetings and family talk.',
};

// What Google's streamGenerateContent?alt=sse sends: thought summaries, then
// the JSON answer split over several events.
function sse(body) {
  const json = JSON.stringify(body);
  const cut = Math.floor(json.length / 2);
  const ev = (o) => `data: ${JSON.stringify(o)}\r\n\r\n`;
  return ev({ candidates: [{ content: { parts: [{ text: 'Working…', thought: true }] } }] })
    + ev({ candidates: [{ content: { parts: [{ text: json.slice(0, cut) }] } }] })
    + ev({ candidates: [{ content: { parts: [{ text: json.slice(cut) }] }, finishReason: 'STOP' }] });
}
const ok = (body) => new Response(sse(body), { status: 200, headers: { 'content-type': 'text/event-stream' } });
const fail = (status, message = 'This model is currently experiencing high demand.') => new Response(JSON.stringify({ error: { message } }), { status });
const isStep1 = (init) => !!JSON.parse(init.body).generationConfig.responseSchema.properties.turns;

// statusFor(url, callNo, init) -> 200 | 'network' | 400 | 5xx...
function google(calls, statusFor = () => 200) {
  return async (url, init) => {
    calls.push({ url, init });
    const status = statusFor(url, calls.length, init);
    if (status === 'network') throw new TypeError('Load failed');   // iPhone Safari's wording
    if (status === 400) return fail(400, 'Unknown name "thinkingLevel" at generation_config.thinking_config');
    if (status !== 200) return fail(status);
    return ok(isStep1(init) ? { turns: TURNS } : ORDER);
  };
}
const noSleep = () => Promise.resolve();

test('step 1 sends the recording, step 2 sends the numbered conversation', async () => {
  const calls = [];
  const statuses = [];
  const audio = new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/mp4' });
  const res = await analyzeConversation({ audio }, {
    apiKey: 'KEY', model: 'gemini-3.8-flash', fetchImpl: google(calls), onStatus: (m) => statuses.push(m),
    customStyles: [{ code: 'JFS 2409', shape: 'Net', rate: 640 }], corrections: [['up icd', 'ruby icd']],
  });
  assert.equal(calls.length, 2);
  for (const c of calls) {
    assert.match(c.url, /\/v1beta\/models\/gemini-3\.8-flash:streamGenerateContent\?alt=sse$/);
    assert.equal(c.init.headers['x-goog-api-key'], 'KEY');
  }
  // step 1: audio + transcribe-only instructions, light thinking
  const r1 = JSON.parse(calls[0].init.body);
  assert.deepEqual(r1.contents[0].parts[0].inline_data, { mime_type: 'audio/m4a', data: Buffer.from([1, 2, 3, 4]).toString('base64') });
  assert.match(r1.system_instruction.parts[0].text, /Do NOT decide what the order is and do NOT leave anything out/);
  assert.deepEqual(r1.generationConfig.thinkingConfig, { thinkingLevel: 'low', includeThoughts: true });
  // step 2: no audio, the numbered conversation, the full price list, careful thinking
  const r2 = JSON.parse(calls[1].init.body);
  assert.ok(!r2.contents[0].parts.some((p) => p.inline_data));
  assert.match(r2.contents[0].parts[0].text, /\[7\] Rep: Sahak Cloth Store, Dibrugarh likh raha hoon\./);
  const sys2 = r2.system_instruction.parts[0].text;
  assert.match(sys2, /STEP A - Customer/);
  assert.match(sys2, /STEP C - Ordered or only asked about/);
  assert.match(sys2, /ruby-icd \| RUBY INT COLOR DRAWER ICD/);
  assert.match(sys2, /JFS 2409 \| shape Net/);
  assert.match(sys2, /up icd = ruby icd/);
  assert.deepEqual(r2.generationConfig.thinkingConfig, { thinkingLevel: 'medium', includeThoughts: true });
  assert.ok(statuses.some((m) => m.startsWith('Step 1 of 2')));
  assert.ok(statuses.some((m) => m.startsWith('Step 2 of 2')));

  // the result
  assert.deepEqual(res.header, { name: 'Sahak Cloth Store', place: 'Dibrugarh', transport: undefined });
  assert.equal(res.transcript.split('\n')[6], '[7] Rep: Sahak Cloth Store, Dibrugarh likh raha hoon.');
  assert.deepEqual(res.lines.map((l) => l.productId), ['ruby-icd', 'ezee-cf-rn', 'lite-icd', 'marcos-cbrief', 'natkhat-rn']);
  assert.deepEqual(res.lines[0].qty, { 85: 3, 90: 10 });
  assert.deepEqual(res.lines[0].turns, [2, 11]);
  assert.equal(res.lines[4].section, 'kids');
  assert.equal(lineTotals(res.lines[2]).amount, 10 * 730);
  assert.deepEqual(res.asked, ['Lite Brief I/E – ordered, then cancelled (turn 9, 11)', 'Classic Gym Vest – rate and scheme (turn 10)']);
  assert.equal(res.model, 'gemini-3.8-flash');
});

test('rebuilding from the written conversation skips step 1', async () => {
  const calls = [];
  const res = await analyzeConversation({ text: turnsToText(TURNS) }, { apiKey: 'K', fetchImpl: google(calls) });
  assert.equal(calls.length, 1);
  assert.ok(!isStep1(calls[0].init));
  assert.equal(res.lines.length, 5);
  assert.deepEqual(textToTurns('[3] Customer: Ezee RN 85 2\nLite ICD 90 3')[1], { n: 2, speaker: 'Other', text: 'Lite ICD 90 3' });
});

test('app-side safety: duplicates merged, bad sizes and missing sizes flagged, never dropped silently', () => {
  const r = toOrderResult({
    customer: { name: '', place: '', transport: '', turns: [] },
    products: [
      { spoken_as: 'Ruby ICD', product_id: 'ruby-icd', shape: 'O/E', status: 'ORDERED', quantities: [{ size: 85, qty: 2 }], turns: [2], heard: 'a' },
      { spoken_as: 'Ruby ICD', product_id: 'ruby-icd', shape: 'O/E', status: 'ORDERED', quantities: [{ size: 90, qty: 3 }, { size: 87, qty: 1 }], turns: [9], heard: 'b' },
      { spoken_as: 'Lite ICD', product_id: 'lite-icd', shape: 'O/E', status: 'ORDERED', quantities: [], turns: [5], heard: 'c' },
      { spoken_as: 'JFS 2409', product_id: '', style_code: 'jfs 2409', shape: 'Net', status: 'ORDERED', quantities: [{ size: 60, qty: 5 }], turns: [6], heard: 'd' },
    ],
    not_order: '',
  });
  assert.equal(r.lines.length, 3);
  assert.deepEqual(r.lines[0].qty, { 85: 2, 90: 3 });           // merged
  assert.deepEqual(r.lines[0].turns, [2, 9]);
  assert.ok(r.warnings.some((w) => /size 87 is not on the form/.test(w)));
  assert.ok(r.warnings.some((w) => /Lite ICD O\/E: ordered but no sizes/.test(w)));
  assert.deepEqual(r.lines[1].qty, {});                           // kept so you can fill it in
  assert.equal(r.lines[2].desc, 'JFS 2409');
  assert.equal(r.lines[2].section, 'kids');
});

test('503 is retried and the order still comes back', async () => {
  const calls = [];
  const statuses = [];
  const res = await analyzeConversation({ text: 'Ruby ICD 85 2' }, {
    apiKey: 'K', sleep: noSleep, onStatus: (m) => statuses.push(m),
    fetchImpl: google(calls, (url, n) => (n <= 2 ? 503 : 200)),
  });
  assert.equal(calls.length, 3);
  assert.equal(res.lines.length, 5);
  assert.ok(statuses.some((m) => /busy or the signal dropped – trying again \(1\/3\)/.test(m)));
});

test('a model that stays overloaded falls back to the next Flash model (and stays there for step 2)', async () => {
  const calls = [];
  const audio = new Blob([new Uint8Array([1])], { type: 'audio/webm' });
  const res = await analyzeConversation({ audio }, {
    apiKey: 'K', model: 'gemini-3.8-flash', sleep: noSleep,
    fetchImpl: google(calls, (url) => (url.includes('gemini-3.8-flash') ? 503 : 200)),
  });
  assert.equal(res.model, 'gemini-3.7-flash');
  assert.equal(calls.filter((c) => c.url.includes('3.8')).length, 4);   // tried once + 3 retries, only in step 1
  assert.equal(calls.filter((c) => c.url.includes('3.7')).length, 2);   // step 1 and step 2
});

test('iPhone "Load failed" (dropped connection) is retried and recovers', async () => {
  const calls = [];
  const res = await analyzeConversation({ text: 'x' }, {
    apiKey: 'K', sleep: noSleep, fetchImpl: google(calls, (url, n) => (n <= 2 ? 'network' : 200)),
  });
  assert.equal(res.lines.length, 5);
});

test('clear messages when it cannot work', async () => {
  await assert.rejects(analyzeConversation({ text: 'x' }, { apiKey: '' }), /API key/);
  await assert.rejects(analyzeConversation({ text: 'x' }, { apiKey: 'K', sleep: noSleep, fetchImpl: google([], () => 'network') }),
    /connection kept dropping.*recording is saved/);
  await assert.rejects(analyzeConversation({ text: 'x' }, { apiKey: 'K', sleep: noSleep, fetchImpl: google([], () => 503) }),
    /overloaded right now.*recording is saved/);
  const calls = [];
  await assert.rejects(analyzeConversation({ text: 'x' }, { apiKey: 'K', fetchImpl: google(calls, () => 403) }), /403/);
  assert.equal(calls.length, 1);   // a refused key is not retried
});

test('a model that rejects the thinking setting is asked again without it', async () => {
  const calls = [];
  const res = await analyzeConversation({ text: 'x' }, {
    apiKey: 'K', sleep: noSleep,
    fetchImpl: google(calls, (url, n, init) => (JSON.parse(init.body).generationConfig.thinkingConfig ? 400 : 200)),
  });
  assert.equal(res.lines.length, 5);
  assert.equal(calls.length, 2);
});

test('recording formats map to types Gemini accepts', () => {
  assert.equal(geminiMime('audio/webm;codecs=opus'), 'audio/webm');   // Chrome / Android
  assert.equal(geminiMime('audio/mp4'), 'audio/m4a');                 // iPhone
  assert.equal(geminiMime('audio/x-m4a'), 'audio/m4a');               // iPhone voice memo
  assert.equal(geminiMime('audio/ogg'), 'audio/ogg');                 // WhatsApp voice note
  assert.equal(geminiMime('audio/mpeg'), 'audio/mpeg');
});

test('instructions cover messy real visits', () => {
  const x = buildExtractInstructions();
  assert.match(x, /asks about a third \(how many colours, how many pieces in a pack/);
  assert.match(x, /same product comes up in several places, it is still ONE entry/);
  assert.match(x, /first asked about and later ordered is ORDERED/);
  assert.match(x, /saat pachasi mein, teen nabbe mein/);
  assert.match(x, /Sahak Cloth Store, Dibrugarh/);
  assert.match(x, /NOT order quantities/);
  assert.match(x, /ezee-wf-rn \| EZEE WHITE FOLDING RN \| shape RN \| box \| 40-45-50@330 55-60-65@370 70@410 73@450 75@600 80-85-90@640 95-100@740/);
  const t = buildTranscribeInstructions();
  assert.match(t, /English letters only/);
});
