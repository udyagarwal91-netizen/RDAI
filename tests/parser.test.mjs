// Run with: npm test   (node --test, no dependencies)
// Transcripts below are the four sample paper order forms, read out loud.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseTranscript, tokenize } from '../js/parser.js';
import { CATALOG, rateFor, CATALOG_BY_ID } from '../js/catalog.js';
import { lineTotals } from '../js/order.js';

const byDesc = (r) => Object.fromEntries(r.lines.map((l) => [l.productId || l.desc, l]));

test('catalog: every product has at least one rate and a unique id', () => {
  const ids = new Set();
  for (const p of CATALOG) {
    assert.ok(!ids.has(p.id), p.id);
    ids.add(p.id);
    assert.ok(Object.keys(p.prices).length > 0, p.id);
  }
  assert.equal(rateFor(CATALOG_BY_ID['ruby-iwd'], 90), 860);
  assert.equal(rateFor(CATALOG_BY_ID['ruby-icd'], 125), 1280);
  assert.equal(rateFor(CATALOG_BY_ID['ruby-iwd'], 75), null);
});

test('Manoj Textiles (catalog items) with small talk in between', () => {
  const r = parseTranscript(
    'party name Manoj Textiles from Sibsagar. Haan bhai kaise ho, sab theek? ' +
    'Ruby IWD 85 2 90 3 95 2 100 2. Classic gym vest 85 3, 95 4, 100 4. ' +
    'Lite ICD 85 aur 90 mein 5 5 box. ditto long trunk O E 85 5 90 10 95 3 100 3. ' +
    'Ruby white rib drawer 85 2 90 2 95 2. Ezee colour R N 85 2 90 2. ' +
    'Marcos colour brief 85 2 90 3 95 1 100 1. Rate kya hai 85 ka?');
  assert.deepEqual(r.header, { name: 'Manoj Textiles', place: 'Sibsagar' });
  const L = byDesc(r);
  assert.deepEqual(L['ruby-iwd'].qty, { 85: 2, 90: 3, 95: 2, 100: 2 });
  assert.deepEqual(L['classic-gym'].qty, { 85: 3, 95: 4, 100: 4 });
  assert.deepEqual(L['lite-icd'].qty, { 85: 5, 90: 5 });
  assert.deepEqual(L['lite-oelt'].qty, { 85: 5, 90: 10, 95: 3, 100: 3 });
  assert.deepEqual(L['ezee-cf-rn'].qty, { 85: 2, 90: 2 });
  assert.deepEqual(L['marcos-cbrief'].qty, { 85: 2, 90: 3, 95: 1, 100: 1 });
  assert.equal(r.lines.length, 7);
  assert.ok(r.ignored.some((s) => s.includes('rate')));
});

test('Handloom Store (spelled-out codes, pocket drawer, dozen items)', () => {
  const r = parseTranscript(
    'Ruby I W D 85 2 90 2 95 1. Ruby I C D 75 1 80 2 85 10 90 10 95 4 100 3. ' +
    'Classic colour trunk 95 2 100 2. Ruby ICD pocket 90 2 95 2. ' +
    'Easy colour R N 75 1 80 1 85 5 90 6 95 3 100 1. Easy colour R N S 85 1 90 2 95 1. Sofia 1100 85 2 90 2 95 2');
  const L = byDesc(r);
  assert.deepEqual(L['ruby-iwd'].qty, { 85: 2, 90: 2, 95: 1 });
  assert.deepEqual(L['ruby-icd'].qty, { 75: 1, 80: 2, 85: 10, 90: 10, 95: 4, 100: 3 });
  assert.deepEqual(L['classic-lt-oe'].qty, { 95: 2, 100: 2 });
  assert.deepEqual(L['ruby-icdp'].qty, { 90: 2, 95: 2 });
  assert.deepEqual(L['ezee-cf-rns'].qty, { 85: 1, 90: 2, 95: 1 });
  assert.deepEqual(L['sofiyaa-1100'].qty, { 85: 2, 90: 2, 95: 2 });
});

test('Shyam Textiles (style codes, kids section, ditto shape and prefix)', () => {
  const r = parseTranscript(
    'customer Shyam Textiles from Sivasagar, send through Assam roadways. ' +
    'ADT 701 WSP minus 20 percent 85 2 90 2 95 2 100 2. ' +
    'JFS 2409 net 60 5 65 5 70 5 75 5 80 5 85 5 box. 2305 net 60 to 85 4 each. ' +
    '2506 same 60 4 65 4 70 4 75 4 80 4 85 4. 2117 WSP minus 20 percent 60 65 70 75 80 85 2 each. ' +
    '2410 60 se 85 tak 2 box');
  assert.equal(r.header.transport, 'Assam Roadways');
  const L = byDesc(r);
  assert.equal(L['ADT 701'].shape, 'WSP (-20%)');
  assert.equal(L['ADT 701'].section, 'adult');
  assert.equal(L['JFS 2409'].section, 'kids');
  assert.deepEqual(L['JFS 2409'].qty, { 60: 5, 65: 5, 70: 5, 75: 5, 80: 5, 85: 5 });
  // range 60-85 skips the 73 column, like the X marks on the paper form
  assert.deepEqual(L['JFS 2305'].qty, { 60: 4, 65: 4, 70: 4, 75: 4, 80: 4, 85: 4 });
  assert.equal(L['JFS 2506'].shape, 'Net');
  assert.equal(L['JFS 2117'].shape, 'WSP (-20%)');
  assert.deepEqual(L['JFS 2410'].qty, { 60: 2, 65: 2, 70: 2, 75: 2, 80: 2, 85: 2 });
});

test('Manoj Textiles page 2 (single-letter codes, zipped lists)', () => {
  const r = parseTranscript(
    'AHW 613 WSP less 15 percent 85 to 100 2 each. AWL 611 same 85 90 95 100 2 2 2 2. ' +
    'KL 44 net 100 1 105 1 110 1. F 46 net 85 to 110 4 each. SP 42 net 85 2 90 2 95 2 100 2 105 2 110 2. ' +
    'JFS 2505 WSP minus 15 percent 60 65 70 75 80 85 4 4 4 4 4 4. SP 12 net 60 4 65 4 70 4 75 4 80 4 85 4');
  const L = byDesc(r);
  assert.deepEqual(L['AWL 611'].qty, { 85: 2, 90: 2, 95: 2, 100: 2 });
  assert.equal(L['AWL 611'].shape, 'WSP (-15%)');
  assert.deepEqual(L['KL 44'].qty, { 100: 1, 105: 1, 110: 1 });
  assert.deepEqual(L['F 46'].qty, { 85: 4, 90: 4, 95: 4, 100: 4, 105: 4, 110: 4 });
  assert.equal(L['SP 12'].section, 'kids');
});

test('spoken number forms', () => {
  const nums = (s) => tokenize(s).filter((t) => t.n != null).map((t) => t.n);
  assert.deepEqual(nums('eighty five two ninety three'), [85, 2, 90, 3]);
  assert.deepEqual(nums('one ten do box one twenty five teen'), [110, 2, 125, 3]);
  assert.deepEqual(nums('85 to 90 to'), [85, 2, 90, 2]);
});

test('ranges, leading quantity and corrections', () => {
  const r = parseTranscript('Ruby ICD 3 box each 85 to 100. Lite brief 85 2. Ruby ICD 85 5. cancel lite brief');
  const L = byDesc(r);
  assert.deepEqual(L['ruby-icd'].qty, { 85: 5, 90: 3, 95: 3, 100: 3 });
  assert.equal(L['lite-iebrief'], undefined);
});

test('pricing: line totals use size-wise rates', () => {
  const r = parseTranscript('Ruby IWD 85 2 95 1 125 1');
  const t = lineTotals(r.lines[0]);
  assert.equal(t.boxes, 4);
  assert.equal(t.amount, 2 * 860 + 960 + 1260);
  assert.deepEqual(t.missingRates, []);
});

test('catalog: product picker labels ("short + shape") are unique', () => {
  const labels = CATALOG.map((p) => `${p.short} ${p.shape}`.trim().toLowerCase());
  const dup = labels.filter((l, i) => labels.indexOf(l) !== i);
  assert.deepEqual(dup, []);
});
