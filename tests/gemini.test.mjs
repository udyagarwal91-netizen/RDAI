// Gemini request/response handling, with a fake fetch (no network).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { analyzeConversation, toOrderResult, geminiMime, buildInstructions } from '../js/gemini.js';
import { lineTotals } from '../js/order.js';

const answer = {
  transcript: 'Rep: Namaste bhai, kaise ho?\nCustomer: Badhiya. Papa ki tabiyat theek hai.\nCustomer: Ruby ICD saat pachasi mein, teen nabbe mein.\nRep: Scheme hai, 10 dabbe par 1 free.\nCustomer: JFS 2409 net, 60 se 85 tak paanch paanch.',
  customer: { name: 'Manoj Textiles', place: 'Sibsagar', transport: '' },
  lines: [
    { product_id: 'ruby-icd', style_code: '', shape: 'O/E', quantities: [{ size: 85, qty: 7 }, { size: 90, qty: 3 }], heard: 'Ruby ICD saat pachasi mein, teen nabbe mein', note: '' },
    { product_id: '', style_code: 'jfs 2409', shape: 'Net', quantities: [60, 65, 70, 75, 80, 85].map((size) => ({ size, qty: 5 })), heard: 'JFS 2409 net 60 se 85 tak paanch paanch', note: '' },
    { product_id: 'lite-icd', style_code: '', shape: 'O/E', quantities: [{ size: 87, qty: 2 }], heard: 'lite icd 87', note: '' },
  ],
  not_order: 'Greetings, family health and the scheme were left out.',
};

function fakeFetch(calls, body = answer, status = 200) {
  return async (url, init) => {
    calls.push({ url, init });
    return {
      ok: status === 200,
      status,
      headers: { get: () => null },
      json: async () => (status === 200
        ? { candidates: [{ finishReason: 'STOP', content: { parts: [{ text: JSON.stringify(body) }] } }] }
        : { error: { message: 'quota' } }),
    };
  };
}

test('sends the whole recording inline with the price list and a JSON schema', async () => {
  const calls = [];
  const audio = new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/webm;codecs=opus' });
  const res = await analyzeConversation({ audio }, {
    apiKey: 'KEY', model: 'gemini-3.8-flash', fetchImpl: fakeFetch(calls),
    customStyles: [{ code: 'JFS 2409', shape: 'Net', rate: 640 }], corrections: [['up icd', 'ruby icd']],
  });
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1beta\/models\/gemini-3\.8-flash:generateContent$/);
  assert.equal(calls[0].init.headers['x-goog-api-key'], 'KEY');
  const req = JSON.parse(calls[0].init.body);
  const audioPart = req.contents[0].parts[0].inline_data;
  assert.equal(audioPart.mime_type, 'audio/webm');
  assert.equal(audioPart.data, Buffer.from([1, 2, 3, 4]).toString('base64'));
  assert.equal(req.generationConfig.responseMimeType, 'application/json');
  assert.ok(req.generationConfig.responseSchema.properties.lines);
  const sys = req.system_instruction.parts[0].text;
  assert.match(sys, /ruby-icd \| RUBY INT COLOR DRAWER ICD/);   // price list included
  assert.match(sys, /JFS 2409 \| shape Net/);                     // own styles included
  assert.match(sys, /up icd = ruby icd/);                         // taught corrections included

  // answer mapped onto order lines
  assert.equal(res.header.name, 'Manoj Textiles');
  assert.match(res.transcript, /^Rep: Namaste/);
  assert.deepEqual(res.lines[0].qty, { 85: 7, 90: 3 });
  assert.equal(res.lines[0].heard, 'Ruby ICD saat pachasi mein, teen nabbe mein');
  assert.equal(lineTotals(res.lines[0]).amount, 7 * 880 + 3 * 880);
  assert.equal(res.lines[1].desc, 'JFS 2409');
  assert.equal(res.lines[1].section, 'kids');
  assert.equal(res.lines.length, 2);                               // size 87 dropped ...
  assert.ok(res.warnings.some((w) => /size 87/.test(w)));          // ... with a warning
  assert.deepEqual(res.ignored, ['Greetings, family health and the scheme were left out.']);
});

test('clear error messages from Gemini failures', async () => {
  await assert.rejects(analyzeConversation({ text: 'x' }, { apiKey: '' }), /API key/);
  const calls = [];
  await assert.rejects(analyzeConversation({ text: 'Ruby ICD 85 2' }, { apiKey: 'K', fetchImpl: fakeFetch(calls, answer, 403) }), /403/);
  assert.equal(calls.length, 1);   // 403 is not retried
});

test('recording formats map to types Gemini accepts', () => {
  assert.equal(geminiMime('audio/webm;codecs=opus'), 'audio/webm');   // Chrome / Android
  assert.equal(geminiMime('audio/mp4'), 'audio/m4a');                 // iPhone Safari/Chrome
  assert.equal(geminiMime('audio/x-m4a'), 'audio/m4a');               // iPhone voice memo
  assert.equal(geminiMime('audio/ogg'), 'audio/ogg');                 // WhatsApp voice note
  assert.equal(geminiMime('audio/mpeg'), 'audio/mpeg');
});

test('instructions cover the Hindi patterns found in real visits', () => {
  const s = buildInstructions();
  assert.match(s, /saat pachasi mein, teen nabbe mein/);
  assert.match(s, /English letters/);
  assert.match(s, /bare "ICD" means Ruby ICD/);
  assert.equal(toOrderResult({ lines: [] }).lines.length, 0);
});

test('price list sent to Gemini lists size groups in size order', () => {
  const s = buildInstructions();
  assert.match(s, /ezee-wf-rn \| EZEE WHITE FOLDING RN \| shape RN \| box \| 40-45-50@330 55-60-65@370 70@410 73@450 75@600 80-85-90@640 95-100@740/);
  assert.match(s, /natkhat-rn \| NATKHAT PRINT RN \| shape RN \| box \| 35@460 40-45-50@490/);
});
