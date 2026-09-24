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

// ---------------------------------------------------------------------------
// Hindi: what Chrome returns when listening in Hindi (Devanagari)
// ---------------------------------------------------------------------------
import { transliterate, devanagariToRoman } from '../js/hindi.js';

test('Hindi: Manoj Textiles order in Devanagari with Hindi numbers', () => {
  const r = parseTranscript(
    'पार्टी का नाम मनोज टेक्सटाइल्स है, सिबसागर से। नमस्ते भाई कैसे हो। ' +
    'रूबी आईडब्ल्यूडी पचासी में दो, नब्बे में तीन, पचानवे में दो, सौ में दो। ' +
    'क्लासिक जिम बनियान पचासी तीन, पचानवे चार, सौ चार। ' +
    'लाइट आईसीडी पचासी और नब्बे में पांच पांच डिब्बे। ' +
    'वही लॉन्ग ट्रंक ओ ई पचासी पांच नब्बे दस पचानवे तीन सौ तीन। ' +
    'मार्कोस कलर ब्रीफ एक सौ दस में चार, एक सौ पांच में दो। ' +
    'रेट क्या है भाई पचासी का');
  assert.deepEqual(r.header, { name: 'मनोज टेक्सटाइल्स', place: 'सिबसागर' });
  const L = byDesc(r);
  assert.deepEqual(L['ruby-iwd'].qty, { 85: 2, 90: 3, 95: 2, 100: 2 });
  assert.deepEqual(L['classic-gym'].qty, { 85: 3, 95: 4, 100: 4 });
  assert.deepEqual(L['lite-icd'].qty, { 85: 5, 90: 5 });
  assert.deepEqual(L['lite-oelt'].qty, { 85: 5, 90: 10, 95: 3, 100: 3 });
  assert.deepEqual(L['marcos-cbrief'].qty, { 105: 2, 110: 4 });
  assert.equal(r.lines.length, 5);
});

test('Hindi: ranges (से…तक), "हर साइज़", "चार चार", cancel, style codes', () => {
  const r = parseTranscript(
    'रूबी आई सी डी पचहत्तर से सौ तक हर साइज़ में दो डिब्बे। इज़ी कलर आरएन 85 में 2 90 में 3। ' +
    'नटखट प्रिंट बनियान साठ पैंसठ सत्तर में चार चार चार। लाइट ब्रीफ पचासी दो। लाइट ब्रीफ हटा दो। ' +
    'जेएफएस 2409 नेट साठ में पांच पैंसठ में पांच। 2505 वही साठ से पचासी तक चार चार डिब्बे। इसको अभी दे दो भाई');
  const L = byDesc(r);
  assert.deepEqual(L['ruby-icd'].qty, { 75: 2, 80: 2, 85: 2, 90: 2, 95: 2, 100: 2 });
  assert.deepEqual(L['ezee-cf-rn'].qty, { 85: 2, 90: 3 });
  assert.deepEqual(L['natkhat-rn'].qty, { 60: 4, 65: 4, 70: 4 });
  assert.equal(L['lite-iebrief'], undefined);
  assert.equal(L['JFS 2409'].shape, 'Net');
  assert.deepEqual(L['JFS 2505'].qty, { 60: 4, 65: 4, 70: 4, 75: 4, 80: 4, 85: 4 });
  assert.equal(r.lines.length, 5);
});

test('Hinglish typed/recognised in Roman letters with Hindi numbers', () => {
  const r = parseTranscript(
    'party ka naam Handloom Store hai, Tinsukia se. Ruby IWD pachasi mein do, nabbe mein do, pachanve mein ek. ' +
    'Ruby ICD pachattar ek assi do pachasi das nabbe das. Ezee colour RN pachasi se sau tak do do dabbe. likh do bhai');
  assert.deepEqual(r.header, { name: 'Handloom Store', place: 'Tinsukia' });
  const L = byDesc(r);
  assert.deepEqual(L['ruby-iwd'].qty, { 85: 2, 90: 2, 95: 1 });
  assert.deepEqual(L['ruby-icd'].qty, { 75: 1, 80: 2, 85: 10, 90: 10 });
  assert.deepEqual(L['ezee-cf-rn'].qty, { 85: 2, 90: 2, 95: 2, 100: 2 });
});

test('Hindi: a pause between speech results keeps sizes on the same product', () => {
  const r = parseTranscript('रूबी आईडब्ल्यूडी पचासी में दो\nनब्बे में तीन\nपचानवे में दो');
  assert.deepEqual(r.lines[0].qty, { 85: 2, 90: 3, 95: 2 });
});

test('Hindi: transliteration fallback for words not in the dictionary', () => {
  assert.equal(transliterate('फोल्डिंग'), 'folding');
  assert.equal(transliterate('नटखट'), 'natkhat');
  assert.equal(transliterate('चुनमुन'), 'chunmun');
  assert.equal(transliterate('मार्कोस'), 'markos');
  assert.match(devanagariToRoman('आरएनएस'), /\bRNS\b/);
  // "दे दो" (give) is not the number two
  assert.equal(parseTranscript('रूबी आईसीडी पचासी दे दो').lines.length, 0);
});

test('Hindi: a real shop visit - orders scattered between personal talk, scheme and rate questions', () => {
  const r = parseTranscript([
    'नमस्ते भाई साहब कैसे हो', 'सब बढ़िया है आप बताओ घर पर सब ठीक है', 'पापा की तबीयत कैसी है अब',
    'हाँ अब ठीक है दो दिन पहले डॉक्टर को दिखाया था', 'अच्छा बताइए इस बार क्या चाहिए',
    'रूबी आईडब्ल्यूडी पचासी में दो नब्बे में तीन पचानवे में दो',
    'लाइट आईसीडी पचासी और नब्बे में पांच पांच डिब्बे',
    'इसमें स्कीम क्या चल रही है', 'दस डिब्बे पर एक डिब्बा फ्री है',
    'और कलर चार्ट दिखाओ लाइट आईसीडी में कितने कलर हैं', 'पांच कलर हैं भाई',
    'रूबी आईसीडी का रेट पचासी में कितना है', 'आठ सौ अस्सी', 'ठीक है',
    'क्लासिक जिम बनियान पचासी तीन पचानवे चार सौ चार', 'मार्कोस कलर ब्रीफ पचासी दो नब्बे तीन',
    'बच्चों की शादी कब है', 'अगले महीने है भाई जरूर आना', 'हाँ हाँ जरूर',
    'अच्छा नटखट प्रिंट बनियान साठ पैंसठ सत्तर में चार चार डिब्बे',
    'और रूबी आईडब्ल्यूडी में पचासी तीन कर दो', 'पेमेंट तीस दिन में कर देंगे',
  ].join('\n'));
  const L = byDesc(r);
  assert.deepEqual(Object.keys(L).sort(), ['classic-gym', 'lite-icd', 'marcos-cbrief', 'natkhat-rn', 'ruby-iwd']);
  assert.deepEqual(L['ruby-iwd'].qty, { 85: 3, 90: 3, 95: 2 });   // later correction wins
  assert.deepEqual(L['lite-icd'].qty, { 85: 5, 90: 5 });
  assert.deepEqual(L['natkhat-rn'].qty, { 60: 4, 65: 4, 70: 4 });
});

test('Hindi shown in English letters parses the same as the Devanagari', async () => {
  const { toRomanScript } = await import('../js/hindi.js');
  const hindi = 'पार्टी का नाम मनोज टेक्सटाइल्स है, सिबसागर से।\nनमस्ते भाई कैसे हो\n'
    + 'रूबी आईडब्ल्यूडी पचासी में दो नब्बे में तीन पचानवे में दो सौ में दो\n'
    + 'इसमें स्कीम क्या चल रही है\nदस डिब्बे पर एक डिब्बा फ्री है\n'
    + 'मार्कोस कलर ब्रीफ एक सौ दस में चार, एक सौ पांच में दो\n'
    + 'नटखट प्रिंट बनियान साठ पैंसठ सत्तर में चार चार डिब्बे\nलाइट ब्रीफ पचासी दो\nलाइट ब्रीफ हटा दो\nलिख दो भाई';
  const roman = toRomanScript(hindi);
  assert.ok(!/[ऀ-ॿ]/.test(roman));
  assert.match(roman, /^Party ka naam manoj textiles hai, sibsagar se\./);
  assert.match(roman, /Ruby IWD 85 mein do 90 mein 3/);
  const a = parseTranscript(hindi);
  const b = parseTranscript(roman);
  const strip = (r) => r.lines.map((l) => [l.productId || l.desc, l.qty]);
  assert.deepEqual(strip(b), strip(a));
  assert.deepEqual(b.header, { name: 'Manoj Textiles', place: 'Sibsagar' });
  assert.deepEqual(byDesc(b)['marcos-cbrief'].qty, { 105: 2, 110: 4 });
});

// ---------------------------------------------------------------------------
// Brand names the phone mishears
// ---------------------------------------------------------------------------
import { fixMisheard, setCorrections } from '../js/parser.js';

test('misheard brands before a product word are repaired (UP ICD = Ruby ICD)', () => {
  const cases = [
    ['यूपी आईसीडी पचासी में दो नब्बे में तीन', 'ruby-icd', { 85: 2, 90: 3 }],
    ['UP ICD 85 mein do', 'ruby-icd', { 85: 2 }],
    ['रुपए आईसीडी पचासी दो', 'ruby-icd', { 85: 2 }],
    ['रवि आईसीडी पचासी दो', 'ruby-icd', { 85: 2 }],
    ['Ravi white drawer 85 2', 'ruby-iwd', { 85: 2 }],
    ['रूबी आईसीटी पचासी दो', 'ruby-icd', { 85: 2 }],
    ['लेट आईसीडी पचासी दो', 'lite-icd', { 85: 2 }],
    ['Late long trunk O E 85 5', 'lite-oelt', { 85: 5 }],
    ['मार्क्स कलर ब्रीफ पचासी दो', 'marcos-cbrief', { 85: 2 }],
    ['Gents brief 85 2', 'genteez-brief', { 85: 2 }],
    ['आईसीडी पचासी दो नब्बे तीन', 'ruby-icd', { 85: 2, 90: 3 }],   // bare ICD = Ruby ICD, not kids
  ];
  for (const [text, id, qty] of cases) {
    const r = parseTranscript(text);
    assert.equal(r.lines.length, 1, text);
    assert.equal(r.lines[0].productId, id, text);
    assert.deepEqual(r.lines[0].qty, qty, text);
  }
  // but ordinary talk is left alone
  assert.equal(parseTranscript('ravi bhai kaise ho, 85 2').lines.length, 0);
  assert.equal(fixMisheard('Up to 100 ruby'), 'Up to 100 ruby');
});

test('transcript display shows the corrected brand', () => {
  assert.equal(fixMisheard('UP ICD 85 mein do'), 'Ruby ICD 85 mein do');
  assert.equal(fixMisheard('Ravi I C D 85 mein 2'), 'Ruby I C D 85 mein 2');
  assert.equal(fixMisheard('Ruby ICT 85 do'), 'Ruby ICD 85 do');
});

test('user corrections from Settings apply to text and order', () => {
  setCorrections([['tension', 'natkhat'], ['double', 'ruby']]);
  const r = parseTranscript('tension print vest 60 4. double IWD 85 2');
  assert.deepEqual(r.lines.map((l) => l.productId), ['natkhat-rn', 'ruby-iwd']);
  assert.equal(fixMisheard('Tension print vest'), 'natkhat print vest');
  setCorrections([]);
});
