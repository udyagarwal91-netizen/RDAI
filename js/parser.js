// Offline, rule-based order parser.
//
// Turns a free-flowing conversation transcript ("Ruby IWD 85 mein 2, 90 3 ...
// aur Lite ICD 85 90 5 5 box") into structured order lines. Anything that is
// not a product followed by size/quantity numbers is treated as conversation
// and ignored.
//
// Pipeline: normalise text -> tokenise -> split into segments (product words
// followed by numbers) -> resolve product -> read size/qty pairs.

import { CATALOG, CATALOG_BY_ID, ADULT_SIZES, KIDS_SIZES, BRANDS } from './catalog.js';
import { devanagariToRoman, hasDevanagari, cleanDevanagari, ROMAN_HINDI_NUMBERS } from './hindi.js';

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

// Spoken / mis-recognised forms -> canonical token.
const SYNONYMS = {
  easy: 'ezee', ezy: 'ezee', ezi: 'ezee', izi: 'ezee', eazy: 'ezee', ezee: 'ezee', easi: 'ezee', ez: 'ezee',
  easyline: 'ezeeline', ezeline: 'ezeeline',
  advance: 'advans', advanced: 'advans', advans: 'advans', advantage: 'advans',
  rubi: 'ruby', rooby: 'ruby', rubby: 'ruby',
  classik: 'classic', clasic: 'classic',
  chunmun: 'chunmun', chunnmunn: 'chunmun', chunnu: 'chunmun',
  gentees: 'genteez', gentis: 'genteez', gentiz: 'genteez', gentes: 'genteez', genties: 'genteez',
  marcus: 'marcos', markos: 'marcos', marco: 'marcos', markus: 'marcos', marks: 'marcos',
  natkat: 'natkhat', nutkhat: 'natkhat', notkhat: 'natkhat', natkhut: 'natkhat', natkhatt: 'natkhat', natkhats: 'natkhat',
  hani: 'honey', honney: 'honey',
  lovly: 'lovely',
  sofia: 'sofiyaa', sofiya: 'sofiyaa', soffiya: 'sofiyaa', sophia: 'sofiyaa', sofiyaa: 'sofiyaa', sufiya: 'sofiyaa', safiya: 'sofiyaa',
  light: 'lite', lites: 'lite', lights: 'lite',
  kareena: 'karina', karena: 'karina',
  lilly: 'lily', lili: 'lily',
  // product words
  panty: 'penteez', panties: 'penteez', penty: 'penteez', penties: 'penteez', pantis: 'penteez', panteez: 'penteez', pantyz: 'penteez',
  jockey: 'jokee', jokey: 'jokee', joki: 'jokee', jocky: 'jokee',
  drawers: 'drawer', draw: 'drawer', drower: 'drawer', drawar: 'drawer', underwear: 'drawer',
  colour: 'color', colours: 'color', colors: 'color', coloured: 'color', colored: 'color', col: 'color',
  vests: 'vest', banian: 'vest', baniyan: 'vest', baniyaan: 'vest', banyan: 'vest',
  jim: 'gym', gim: 'gym',
  trunks: 'trunk', trunck: 'trunk',
  briefs: 'brief', breif: 'brief', breef: 'brief',
  boxers: 'boxer', bermudas: 'bermuda', bloomers: 'bloomer',
  printed: 'print', prints: 'print', printing: 'print',
  pockets: 'pocket', pokit: 'pocket', packet: 'pocket',
  plane: 'plain',
  whites: 'white', grey: 'grey', gray: 'grey',
  packer: 'parker', parkar: 'parker',
  inter: 'interlock', interlok: 'interlock',
  samiz: 'sameez', shameez: 'sameez', sameej: 'sameez', samij: 'sameez',
  sports: 'sporto', sporty: 'sporto',
  short: 'shorts',
  tshirts: 'tshirt',
  highcut: 'hicut',
  stretch: 'lycra', licra: 'lycra', lykra: 'lycra',
  frenchie: 'frenchy',
  // shapes / codes
  rns: 'rns', rnr: 'rns', rn: 'rn', sleeve: 'rns', sleeves: 'rns', sleeved: 'rns', baju: 'rns',
  oe: 'oe', ie: 'ie', fe: 'fe', icd: 'icd', iwd: 'iwd', icdp: 'icdp', rcd: 'rcd', cd: 'cd', cj: 'cj', ipd: 'ipd', opd: 'opd',
  wsp: 'wsp',
  // romanised Hindi / transliterated Devanagari forms
  lait: 'lite', laait: 'lite', klasik: 'classic', klasic: 'classic', kalar: 'color', kalard: 'color', rangeen: 'color',
  droar: 'drawer', drovar: 'drawer', dravar: 'drawer', drayar: 'drawer', dror: 'drawer', chaddi: 'drawer', kachha: 'drawer',
  brif: 'brief', trank: 'trunk', edvans: 'advans', chunamun: 'chunmun', jentiz: 'genteez', jentis: 'genteez',
  lavli: 'lovely', lavali: 'lovely', penti: 'penteez', pentiz: 'penteez', painti: 'penteez', blumar: 'bloomer',
  shamiz: 'sameez', vhait: 'white', vait: 'white', safed: 'white', poket: 'pocket', pokit: 'pocket', jeb: 'pocket',
  baramuda: 'bermuda', barmuda: 'bermuda', boksar: 'boxer', baksar: 'boxer', suparfain: 'superfine',
  chenlok: 'chainlock', chainlok: 'chainlock', intarlok: 'interlock', natakhat: 'natkhat', rubee: 'ruby',
  baniyain: 'vest', baniyan: 'vest', banyaan: 'vest', lanng: 'long', lamba: 'long', printed: 'print', chhapai: 'print',
  sada: 'plain', kala: 'black', kali: 'black', sofiyaa: 'sofiyaa', res: 'race', honi: 'honey', hani: 'honey',
  fold: 'folding', foldin: 'folding', stretch: 'lycra', nikkar: 'shorts',
};

// Words that glue numbers together but carry no product meaning.
const CONNECTORS = new Set([
  'box', 'boxes', 'dabba', 'dabbe', 'dibba', 'pcs', 'pc', 'piece', 'pieces', 'dozen', 'doz', 'packet', 'packets',
  'size', 'sizes', 'number', 'no', 'mein', 'me', 'main', 'mai', 'in', 'of', 'ka', 'ke', 'ki', 'and', 'aur', 'or',
  'also', 'bhi', 'then', 'phir', 'fir', 'next', 'plus', 'comma', 'wala', 'wali', 'wale', 'the', 'a', 'an', 'is', 'hai',
  'x', 'cross', 'nil', 'set', 'sets', 'quantity', 'qty', 'ok', 'okay', 'haan', 'ha', 'ji', 'sir', 'bhai', 'bhaiya',
  'dena', 'dijiye', 'dedo', 'de', 'chahiye', 'lagao', 'likho', 'likh', 'write', 'put', 'give', 'send', 'bhejo', 'please',
]);
const RANGE_WORDS = new Set(['to', 'se', 'till', 'tak', 'upto', 'until', 'through', 'thru']);
const EACH_WORDS = new Set(['each', 'har', 'sab', 'all', 'sabme', 'sabmein', 'sabhi', 'every', 'per', 'saare', 'sare', 'sabko']);
const CANCEL_WORDS = new Set(['cancel', 'remove', 'hatao', 'hata', 'delete', 'cut', 'mat', 'nahi', 'nahin']);
const SAME_WORDS = new Set(['ditto', 'same', 'wahi', 'vahi', 'usme', 'usi']);
const SHAPE_WORDS = new Set(['net', 'wsp', 'less', 'minus', 'discount', 'percent', 'wholesale', 'off']);

// Short common words that must never be read as a style-code prefix ("SP 42").
const NOT_CODE = new Set([
  ...CONNECTORS, ...RANGE_WORDS, ...EACH_WORDS, ...CANCEL_WORDS, ...SAME_WORDS, ...SHAPE_WORDS,
  'i', 'we', 'you', 'he', 'she', 'it', 'they', 'my', 'our', 'your', 'at', 'on', 'for', 'by', 'up', 'if', 'so', 'as',
  'do', 'go', 'be', 'am', 'are', 'was', 'what', 'how', 'why', 'who', 'yes', 'hi', 'hello', 'kya', 'kab', 'kitna',
  'rate', 'price', 'order', 'total', 'only', 'just', 'more', 'less', 'make', 'take', 'this', 'that', 'with', 'will',
  'can', 'not', 'but', 'got', 'get', 'let', 'see', 'one', 'ek', 'aap', 'hum', 'ye', 'yeh', 'woh', 'wo', 'ab', 'abhi',
  'aaj', 'kal', 'din', 'mal', 'maal', 'party', 'bill', 'rs', 'rupees', 'rupee', 'paisa', 'amount', 'item', 'items',
]);

// "de do", "likh do": Hindi verb + "do" (give/do), not the number two.
const DO_VERBS = new Set(['de', 'kar', 'karo', 'likh', 'bhej', 'bata', 'rakh', 'laga', 'dikha', 'bol', 'hata', 'chala', 'ban', 'bana', 'jama', 'pack']);

// Numbers that are part of product names rather than sizes/quantities.
const NAME_NUMBERS = new Set([502, 1100, 1200, 1300, 2100, 2200, 3100, 3200]);

const EN_NUMBERS = {
  zero: 0, one: 1, two: 2, too: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
  nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90,
};
const HI_NUMBERS = ROMAN_HINDI_NUMBERS;
const TENS = new Set([20, 30, 40, 50, 60, 70, 80, 90]);

function norm(s) {
  return String(s || '').toLowerCase()
    .replace(/\b([a-z])[./](?=[a-z]\b)/g, '$1')   // O/E -> oe, I.C.D -> icd
    .replace(/\b([a-z])\.(?=[a-z]\b)/g, '$1')
    .replace(/[^a-z0-9]+/g, ' ').trim();
}

function tokensOf(s) {
  return norm(s).split(' ').filter(Boolean).map((t) => SYNONYMS[t] || t);
}

// Keyword sets per catalog product, plus inverse document frequency weights.
function buildIndex(products) {
  const df = new Map();
  const entries = products.map((p) => {
    const kw = new Set([...tokensOf(p.name), ...tokensOf(p.short), ...tokensOf(p.shape)]);
    for (const b of BRANDS) if (p.aliases.some((a) => tokensOf(a).includes(b))) kw.add(b);
    const aliases = p.aliases.map(tokensOf);
    for (const a of aliases) for (const t of a) kw.add(t);
    for (const k of kw) df.set(k, (df.get(k) || 0) + 1);
    return { product: p, kw, aliases, brand: BRANDS.find((b) => kw.has(b)) || null };
  });
  const n = products.length;
  const idf = (t) => Math.log(n / (df.get(t) || n)) + 0.5;
  const vocab = new Set(df.keys());
  return { entries, idf, vocab };
}

let INDEX = buildIndex(CATALOG);
let CUSTOM = [];   // user-defined styles from settings: {code, shape, section, rate}

// Register user-defined styles (e.g. "JFS 2409" with a fixed rate).
export function setCustomProducts(list) {
  CUSTOM = (list || []).filter((c) => c && c.code).map((c) => ({ ...c, key: norm(c.code).replace(/ /g, '') }));
}

// ---------------------------------------------------------------------------
// Text normalisation
// ---------------------------------------------------------------------------

function levenshtein(a, b) {
  if (Math.abs(a.length - b.length) > 2) return 3;
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i]);
  for (let j = 1; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
  }
  return dp[a.length][b.length];
}

const fuzzyCache = new Map();
function fuzzyVocab(t) {
  if (fuzzyCache.has(t)) return fuzzyCache.get(t);
  let best = null;
  if (t.length >= 5 && !NOT_CODE.has(t)) {
    const max = t.length >= 8 ? 2 : 1;
    let bestD = max + 1;
    for (const v of INDEX.vocab) {
      if (v.length < 4) continue;
      const d = levenshtein(t, v);
      if (d < bestD) { bestD = d; best = v; }
    }
  }
  fuzzyCache.set(t, best);
  return best;
}

const isSizeValue = (n) => (n >= 30 && n <= 130 && n % 5 === 0) || n === 73;

function preNormalise(text) {
  // Hindi (Devanagari) speech output -> romanised Hinglish first
  let s = ` ${applyCorrections(devanagariToRoman(applyCorrections(text))).toLowerCase()} `;
  // Devanagari digits -> ASCII
  s = s.replace(/[०-९]/g, (d) => String(d.charCodeAt(0) - 0x966));
  s = s.replace(/%/g, ' percent ');
  // i.c.d / o/e / m/s -> icd / oe / ms
  s = s.replace(/\b([a-z])[./]([a-z])[./]?([a-z])?\b\.?/g, (_, a, b, c) => a + b + (c || ''));
  // sentence ends / separate speech results are hard boundaries
  s = s.replace(/[.?!;\n\u0964]+/g, ' qqbrk ');
  const phrases = [
    [/\brace\s*(4|four|for)\b/g, ' race4 '],
    [/\b(easy|ezee|ezy|eazy)\s*line\b/g, ' ezeeline '],
    [/\b(hi|high)[\s-]*cut\b/g, ' hicut '],
    [/\bup\s*to\b/g, ' upto '],
    [/\bt[\s-]*shirts?\b/g, ' tshirt '],
    [/\bround\s*neck\s*(with\s*)?(half\s*)?sleeves?\b/g, ' rns '],
    [/\bround\s*neck\b/g, ' rn '],
    [/\b(half|with|short)\s*sleeves?\b/g, ' rns '],
    [/\binner\s*elastic\b/g, ' ie '],
    [/\bouter\s*elastic\b/g, ' oe '],
    [/\bfront\s*elastic\b/g, ' fe '],
    [/\bchain\s*lock\b/g, ' chainlock '],
    [/\bsuper\s*fine\b/g, ' superfine '],
    [/\binter\s*lock\b/g, ' interlock '],
    [/\bgym\s*vest\b/g, ' gym vest '],
    [/\bwhole\s*sale\b/g, ' wsp '],
    [/\bw\s*s\s*p\b/g, ' wsp '],
  ];
  for (const [re, rep] of phrases) s = s.replace(re, rep);
  // "85-100" -> range, "5-5" -> two quantities
  s = s.replace(/(\d+)\s*-\s*(\d+)/g, (_, a, b) => (isSizeValue(+a) && isSizeValue(+b) && +b > +a ? `${a} to ${b}` : `${a} ${b}`));
  // Split letter/digit runs: "sp12" -> "sp 12" (race4 is protected above)
  s = s.replace(/\b(?!race4\b)([a-z]+)(\d+)\b/g, '$1 $2');
  s = s.replace(/\b(\d+)([a-z]+)\b/g, '$1 $2');
  return s.replace(/[^a-z0-9 ]+/g, ' ');
}

function wordNumber(tokens, i) {
  // returns [value, consumed] for number words at tokens[i]
  const t = tokens[i];
  const next = tokens[i + 1];
  const small = (w) => (w == null ? null : /^\d+$/.test(w) ? Number(w) : EN_NUMBERS[w] ?? HI_NUMBERS[w] ?? null);
  // A bare "sau" / "hundred" is size 100 ("सौ चार" = size 100, 4 boxes);
  // only "एक सौ दस" / "one hundred ten" make 110.
  if (t === 'hundred' || t === 'sau') return [100, 1];
  // "1 sau 10" / "ek sau das" / "एक सौ दस" -> 110
  if ((t === '1' || t === 'ek') && (next === 'hundred' || next === 'sau')) {
    const v = small(tokens[i + 2]);
    if (v != null && v > 0 && v <= 30) return [100 + v, 3];
    return [100, 2];
  }
  if (/^\d+$/.test(t)) return [Number(t), 1];
  // "one ten", "one twenty five", "one hundred five"
  if (t === 'one' && next != null) {
    if (next === 'hundred') {
      const n2 = tokens[i + 2];
      if (n2 != null && (EN_NUMBERS[n2] != null || /^\d+$/.test(n2))) {
        const v = EN_NUMBERS[n2] != null ? EN_NUMBERS[n2] : Number(n2);
        if (v <= 30) return [100 + v, 3];
      }
      return [100, 2];
    }
    if (next === 'oh' && tokens[i + 2] === 'five') return [105, 3];
    const nv = EN_NUMBERS[next];
    if (nv === 10 || nv === 15 || nv === 20 || nv === 30) {
      if (nv === 20 && tokens[i + 2] === 'five') return [125, 3];
      return [100 + nv, 2];
    }
  }
  if (EN_NUMBERS[t] != null) {
    const v = EN_NUMBERS[t];
    // "eighty five" -> 85, but "ninety three" is size 90 + qty 3 (93 is no size)
    if (TENS.has(v) && next != null && EN_NUMBERS[next] != null && EN_NUMBERS[next] < 10 && EN_NUMBERS[next] > 0
      && (v < 30 || isSizeValue(v + EN_NUMBERS[next]))) {
      return [v + EN_NUMBERS[next], 2];
    }
    return [v, 1];
  }
  if (HI_NUMBERS[t] != null) return [HI_NUMBERS[t], 1];
  return null;
}

// ---------------------------------------------------------------------------
// Mishearing repair
// ---------------------------------------------------------------------------
// The phone's speech engine does not know our brand names, so "Ruby ICD"
// often arrives as "UP ICD", "रुपए ICD" or "Ravi ICD". A word right before a
// product word (ICD, drawer, vest, colour...) that sounds like a brand is
// taken as that brand.

const BRAND_NAMES = {
  ruby: 'Ruby', lite: 'Lite', ezee: 'Ezee', ezeeline: 'Ezeeline', advans: 'Advans', classic: 'Classic',
  chunmun: 'Chunmun', genteez: 'Genteez', hot: 'Hot', marcos: 'Marcos', natkhat: 'Natkhat', honey: 'Honey',
  race4: 'Race4', lovely: 'Lovely', sofiyaa: 'Sofiyaa', karina: 'Karina', lily: 'Lily',
};

// Known mishearings (romanised), checked before the sound-alike test.
const MISHEARD = {
  up: 'ruby', upi: 'ruby', rupee: 'ruby', rupees: 'ruby', rupe: 'ruby', rupay: 'ruby', rupaye: 'ruby', rupiya: 'ruby',
  rupya: 'ruby', rupye: 'ruby', rubi: 'ruby', roobi: 'ruby', rabi: 'ruby', ravi: 'ruby', robi: 'ruby', rubee: 'ruby',
  ruvi: 'ruby', rumi: 'ruby', lubi: 'ruby', rubby: 'ruby', rb: 'ruby', rubia: 'ruby', rubina: 'ruby', baby: 'ruby',
  late: 'lite', let: 'lite', lait: 'lite', laid: 'lite', like: 'lite', life: 'lite', lyte: 'lite', lights: 'lite',
  lat: 'lite', lete: 'lite',
  easy: 'ezee', isi: 'ezee', isee: 'ezee', izee: 'ezee', eji: 'ezee', ezi: 'ezee', essy: 'ezee', ez: 'ezee', ec: 'ezee', eg: 'ezee',
  marks: 'marcos', mark: 'marcos', markus: 'marcos', marcus: 'marcos', markas: 'marcos', markose: 'marcos', marko: 'marcos',
  marker: 'marcos', marx: 'marcos', mercus: 'marcos', markos: 'marcos',
  classy: 'classic', classics: 'classic', glassic: 'classic', klasik: 'classic', clasik: 'classic', plastic: 'classic',
  natak: 'natkhat', natkhad: 'natkhat', natkat: 'natkhat', nutkhut: 'natkhat', notkhat: 'natkhat',
  sophia: 'sofiyaa', safia: 'sofiyaa', sofia: 'sofiyaa', sufia: 'sofiyaa', sophie: 'sofiyaa', sofie: 'sofiyaa', saifiya: 'sofiyaa',
  gents: 'genteez', gentes: 'genteez', jents: 'genteez', gentis: 'genteez', jentis: 'genteez', gentle: 'genteez',
  advance: 'advans', advent: 'advans', edvans: 'advans', adwans: 'advans',
  carina: 'karina', kareena: 'karina', corona: 'karina', karine: 'karina',
  lovley: 'lovely', lovli: 'lovely', lavli: 'lovely', lavly: 'lovely',
  honi: 'honey', hani: 'honey', hunny: 'honey', hanee: 'honey',
  chunnu: 'chunmun', chunmunn: 'chunmun', lili: 'lily', lilly: 'lily',
};

// Words that may follow a brand. Only these can trigger a repair.
const PRODUCT_NEXT = new Set([
  'icd', 'iwd', 'icdp', 'rcd', 'cd', 'cj', 'ipd', 'opd', 'rn', 'rns', 'oe', 'ie', 'fe',
  'drawer', 'brief', 'trunk', 'vest', 'jokee', 'boxer', 'bermuda', 'penteez', 'bloomer', 'sameez', 'shorts', 'tshirt',
  'gym', 'color', 'white', 'grey', 'black', 'print', 'plain', 'pocket', 'long', 'mini', 'mid', 'lycra', 'folding',
  'interlock', 'chainlock', 'superfine', 'parker', 'rib', 'stripe', 'hicut', 'sporto', 'frenchy', 'premium', 'design',
]);

// Rough sound key for Indian-English speech: b/p/v/w, d/t, g/j/k, s/z alike.
function soundKey(w) {
  let k = w.toLowerCase().replace(/[^a-z]/g, '')
    .replace(/ph/g, 'f').replace(/gh/g, '').replace(/ck/g, 'k').replace(/([kgcsdtb])h/g, '$1')
    .replace(/c(?=[eiy])/g, 's').replace(/[cq]/g, 'k').replace(/x/g, 'ks');
  k = k.replace(/[pvw]/g, 'b').replace(/t/g, 'd').replace(/[gj]/g, 'k').replace(/z/g, 's');
  return k.replace(/[aeiouyh]/g, '').replace(/(.)\1+/g, '$1');
}
const BRAND_KEYS = Object.keys(BRAND_NAMES).map((b) => [b, soundKey(b)]).filter(([, k]) => k.length >= 2);

function brandHasWord(brand, word) {
  return INDEX.entries.some((e) => e.brand === brand && e.kw.has(word));
}

/** If `word` (heard right before `nextWord`) is probably a misheard brand, return the brand. */
export function misheardBrand(word, nextWord) {
  const w = String(word || '').toLowerCase();
  const nx = SYNONYMS[nextWord] || nextWord;
  if (!w || !PRODUCT_NEXT.has(nx)) return null;
  const direct = SYNONYMS[w] || w;
  if (BRANDS.includes(direct)) return null;
  let brand = MISHEARD[w] || null;
  if (!brand && (INDEX.vocab.has(direct) || NOT_CODE.has(w) || CONNECTORS.has(w))) return null;
  if (!brand) {
    const key = soundKey(w);
    if (key.length >= 2) {
      for (const [b, bk] of BRAND_KEYS) {
        if (key === bk || (key.length >= 4 && bk.length >= 4 && levenshtein(key, bk) <= 1)) { brand = b; break; }
      }
    }
  }
  return brand && brandHasWord(brand, nx) ? brand : null;
}

// Product codes misheard by one similar-sounding letter: ICT -> ICD, IVD -> IWD.
const CODE_LIST = ['icd', 'iwd', 'icdp', 'rcd', 'ipd', 'opd', 'rns', 'oe', 'ie'];
const SOUND_CLASS = {};
for (const group of ['dtg', 'wvbu', 'csk', 'nm', 'ea']) for (const c of group) SOUND_CLASS[c] = group;
export function misheardCode(word) {
  const w = String(word || '').toLowerCase();
  if (w.length < 2 || w.length > 4 || INDEX.vocab.has(w) || NOT_CODE.has(w)) return null;
  for (const code of CODE_LIST) {
    if (code.length !== w.length) continue;
    let diff = 0;
    let ok = true;
    for (let i = 0; i < w.length; i++) {
      if (w[i] === code[i]) continue;
      diff++;
      if (!SOUND_CLASS[w[i]] || SOUND_CLASS[w[i]] !== SOUND_CLASS[code[i]]) ok = false;
    }
    if (ok && diff === 1) return code;
  }
  return null;
}

// User-taught corrections from Settings: [["up icd", "ruby icd"], ...]
let CORRECTIONS = [];
export function setCorrections(list) {
  CORRECTIONS = (list || []).filter(([a, b]) => a && b).map(([a, b]) => [
    new RegExp(`(^|[^\\p{L}\\p{N}])${a.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+')}(?=$|[^\\p{L}\\p{N}])`, 'giu'),
    b.trim(),
  ]);
}
export function applyCorrections(text) {
  let s = String(text || '');
  for (const [re, to] of CORRECTIONS) s = s.replace(re, (m, pre) => pre + to);
  return s;
}

/**
 * Tidy a displayed transcript line: user corrections, misheard brands and
 * codes ("UP ICD" -> "Ruby ICD"). Keeps everything else as heard.
 */
export function fixMisheard(text) {
  return applyCorrections(text).split('\n').map((line) => {
    const words = line.split(/(\s+)/);
    const idx = words.map((w, i) => i).filter((i) => words[i].trim());
    const clean = (w) => w.toLowerCase().replace(/[^a-z0-9]/g, '');
    for (let k = 0; k < idx.length; k++) {
      const i = idx[k];
      const w = clean(words[i]);
      // next word, joining spelled letters ("I C D")
      let nx = '';
      for (let j = k + 1; j < idx.length; j++) {
        const c = clean(words[idx[j]]);
        if (c.length === 1 && /[a-z]/.test(c)) { nx += c; continue; }
        if (!nx) nx = c;
        break;
      }
      const nxFixed = misheardCode(nx) || nx;
      const b = misheardBrand(w, nxFixed);
      if (b) { words[i] = words[i].replace(/[A-Za-z0-9]+/, BRAND_NAMES[b]); continue; }
      const code = misheardCode(w);
      const prev = k > 0 ? clean(words[idx[k - 1]]) : '';
      const prevBrand = BRANDS.includes(SYNONYMS[prev] || prev) || MISHEARD[prev];
      if (code && (prevBrand || /^\d/.test(nx))) words[i] = words[i].replace(/[A-Za-z]+/, code.toUpperCase());
    }
    return words.join('');
  }).join('\n');
}

// Produce the token stream: {w: word} or {n: number}
export function tokenize(text) {
  let raw = preNormalise(text).split(/\s+/).filter(Boolean);

  // Collapse spelled-out letters: "i w d" -> "iwd", "r n s" -> "rns"
  const collapsed = [];
  for (let i = 0; i < raw.length; i++) {
    if (/^[a-z]$/.test(raw[i]) && /^[a-z]$/.test(raw[i + 1] || '')) {
      let j = i;
      let w = '';
      while (j < raw.length && /^[a-z]$/.test(raw[j])) w += raw[j++];
      collapsed.push(w);
      i = j - 1;
    } else collapsed.push(raw[i]);
  }
  raw = collapsed;

  const out = [];
  for (let i = 0; i < raw.length; i++) {
    const t = raw[i];
    const prev = out[out.length - 1];
    const prevIsNum = prev && prev.n != null;
    // Hindi "do" (2) vs English "do"
    if (t === 'do') {
      const nx = raw[i + 1];
      const before = raw[i - 1];
      const verb = DO_VERBS.has(before);
      const afterNumber = prevIsNum || (/^(mein|me|men|main|mai|ka|ke|ki|of|in)$/.test(before || '')
        && out[out.length - 2] && out[out.length - 2].n != null);
      if (!verb && (afterNumber || CONNECTORS.has(nx) || EACH_WORDS.has(nx) || /^\d+$/.test(nx || ''))) {
        out.push({ n: 2, src: t });
      }
      continue;
    }
    // "for" misheard for four right after a size
    if (t === 'for' && prevIsNum && isSizeValue(prev.n)) { out.push({ n: 4, src: t }); continue; }
    // "to" is either a range or "two"
    if (t === 'to') {
      const nx = wordNumber(raw, i + 1);
      const after = raw[i + 1 + (nx ? nx[1] : 0)];
      const pairStyle = after === 'to' || after === 'two' || after === 'too';
      if (prevIsNum && isSizeValue(prev.n) && nx && isSizeValue(nx[0]) && nx[0] > prev.n && !pairStyle) {
        out.push({ w: 'to' });
      } else if (prevIsNum && isSizeValue(prev.n)) {
        out.push({ n: 2, src: t });
      } else out.push({ w: 'to' });
      continue;
    }
    if (t === 'qqbrk') { if (prev && !prev.brk) out.push({ brk: true }); continue; }
    const num = wordNumber(raw, i);
    if (num) {
      out.push({ n: num[0], src: t });
      i += num[1] - 1;
      continue;
    }
    let w = SYNONYMS[t] || t;
    if (!INDEX.vocab.has(w) && !CONNECTORS.has(w) && !RANGE_WORDS.has(w) && !EACH_WORDS.has(w)) {
      const nxRaw = raw[i + 1] || '';
      const nx = misheardCode(nxRaw) || SYNONYMS[nxRaw] || nxRaw;
      const brand = misheardBrand(t, nx);
      const code = !brand && misheardCode(w);
      const f = brand || code || fuzzyVocab(w);
      if (f) w = f;
    }
    out.push({ w });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Segmentation
// ---------------------------------------------------------------------------

const isVocab = (w) => INDEX.vocab.has(w) && !CONNECTORS.has(w);

function looksLikeCode(tokens, i) {
  const t = tokens[i];
  if (!t.w || !/^[a-z]{1,4}$/.test(t.w) || NOT_CODE.has(t.w) || INDEX.vocab.has(t.w)) return false;
  const nx = tokens[i + 1];
  return !!(nx && nx.n != null && (!isSizeValue(nx.n) || nx.n >= 200));
}

function segment(tokens) {
  const segs = [];
  let cur = { name: [], nums: [] };
  const flush = () => { if (cur.name.length || cur.nums.length) segs.push(cur); cur = { name: [], nums: [] }; };

  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.brk) { flush(); continue; }
    if (t.n != null) {
      const n = t.n;
      const last = cur.name[cur.name.length - 1];
      if (!cur.nums.length && cur.name.length && NAME_NUMBERS.has(n)) { cur.name.push(t); continue; }
      if (!cur.nums.length && last && last.code && !last.codeNum) {
        cur.name.push({ ...t, codeNum: true }); last.hasNum = true; continue;
      }
      if (n >= 200 && n <= 99999 && !NAME_NUMBERS.has(n)) {
        // A bare style number such as "2505" starts a new line.
        const prevNum = cur.nums[cur.nums.length - 1];
        const isPercentCtx = prevNum && (prevNum.w === 'minus' || prevNum.w === 'less');
        if (!isPercentCtx) {
          if (cur.nums.length || cur.name.some((x) => x.w)) flush();
          cur.name.push({ ...t, codeNum: true, bare: true });
          continue;
        }
      }
      cur.nums.push(t);
      continue;
    }
    const w = t.w;
    const code = looksLikeCode(tokens, i);
    if (isVocab(w) || code || CANCEL_WORDS.has(w)) {
      // "Lite brief hatao, JFS 2409 ..." - a cancel ends its own line
      const ci = cur.name.findIndex((x) => x.w && CANCEL_WORDS.has(x.w));
      const cancelled = ci > 0 && cur.name.slice(0, ci).some((x) => x.w && isVocab(x.w));
      if (cur.nums.length || (cancelled && !CANCEL_WORDS.has(w))) flush();
      cur.name.push(code ? { ...t, code: true } : t);
      continue;
    }
    if (cur.nums.length && !CONNECTORS.has(w) && !RANGE_WORDS.has(w) && !EACH_WORDS.has(w)
      && !SHAPE_WORDS.has(w) && !SAME_WORDS.has(w)) {
      // Unknown word after the numbers: the order line is over, this is talk.
      flush();
      cur.name.push({ ...t, junk: true });
      continue;
    }
    (cur.nums.length ? cur.nums : cur.name).push(t);
  }
  flush();
  // A short aside inside a line ("85 2, 90 3, sorry, 95 2") should not drop
  // the rest of the line: glue it back onto the previous product segment.
  const merged = [];
  for (const sg of segs) {
    const prev = merged[merged.length - 1];
    const onlyJunk = sg.name.length <= 2 && sg.name.every((t) => t.junk || (t.w && CONNECTORS.has(t.w)));
    // also covers a pause: "Ruby IWD 85 mein do" <new line> "90 mein teen"
    if (prev && onlyJunk && prev.name.some((t) => !t.junk && !(t.w && CANCEL_WORDS.has(t.w)))
      && sg.nums.filter((t) => t.n != null).length >= 2) {
      prev.nums.push(...sg.nums);
    } else merged.push(sg);
  }
  return merged;
}

// ---------------------------------------------------------------------------
// Product resolution
// ---------------------------------------------------------------------------

function scoreProducts(words) {
  const R = new Set(words.filter((w) => INDEX.vocab.has(w)));
  if (!R.size) return [];
  const scored = [];
  for (const e of INDEX.entries) {
    let matched = 0;
    let unmatched = 0;
    for (const k of e.kw) {
      if (R.has(k)) matched += INDEX.idf(k);
      else unmatched += INDEX.idf(k);
    }
    if (!matched) continue;
    let extra = 0;
    for (const r of R) if (!e.kw.has(r)) extra += INDEX.idf(r);
    let alias = 0;
    for (const a of e.aliases) {
      if (!a.every((t) => R.has(t))) continue;
      // exact alias ("icd" alone = Ruby ICD, the house default) wins outright
      alias = Math.max(alias, a.length === R.size ? 6 : a.length * 1.5);
    }
    const score = matched + alias - 0.12 * unmatched - 0.6 * extra;
    scored.push({ product: e.product, score, matched });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored;
}

function brandOf(words) {
  return words.find((w) => BRANDS.includes(w)) || null;
}

function resolveProduct(words, ctx) {
  let ranked = scoreProducts(words);
  // "Ruby IWD ... ICD pocket" - an unbranded name inherits the previous brand
  // (the spoken equivalent of a ditto mark), but only if that scores better.
  if (!brandOf(words) && ctx.lastBrand && words.some((w) => INDEX.vocab.has(w))) {
    const inherited = scoreProducts([...words, ctx.lastBrand]);
    if (inherited[0] && (!ranked[0] || inherited[0].score > ranked[0].score)) ranked = inherited;
  }
  const best = ranked[0];
  if (!best || best.score < 1.2) return null;
  return { product: best.product, confidence: Math.min(1, best.score / 8), alternatives: ranked.slice(1, 4).map((r) => r.product.id) };
}

// ---------------------------------------------------------------------------
// Shape (Net / WSP -15%) extraction
// ---------------------------------------------------------------------------

function extractShape(tokens) {
  const rest = [];
  let net = false;
  let wsp = false;
  let pct = null;
  let same = false;
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.w === 'net') { net = true; continue; }
    if (t.w === 'wsp' || t.w === 'wholesale') { wsp = true; continue; }
    if (t.w && SAME_WORDS.has(t.w)) { same = true; continue; }
    if ((t.w === 'minus' || t.w === 'less' || t.w === 'discount') && tokens[i + 1] && tokens[i + 1].n != null) {
      pct = tokens[i + 1].n; i++;
      if (tokens[i + 1] && (tokens[i + 1].w === 'percent' || tokens[i + 1].w === 'off')) i++;
      continue;
    }
    if (t.n != null && tokens[i + 1] && tokens[i + 1].w === 'percent') { pct = t.n; i++; continue; }
    if (t.w === 'percent' || t.w === 'off') continue;
    rest.push(t);
  }
  let shape = null;
  if (wsp || pct != null) shape = `WSP${pct != null ? ` (-${pct}%)` : ''}`;
  else if (net) shape = 'Net';
  return { shape, same, rest };
}

// ---------------------------------------------------------------------------
// Size / quantity reading
// ---------------------------------------------------------------------------

function expandRange(a, b, cols) {
  return cols.filter((s) => s >= a && s <= b && (s !== 73 || a === 73 || b === 73));
}

export function readQuantities(tokens, cols) {
  const warnings = [];
  // 1. classify - any real size (30..130, 73) is a size, even if this
  // product's price list doesn't carry it (a misheard product must not turn
  // "90 mein 3" into quantities).
  const colSet = { has: (n) => isSizeValue(n) || cols.includes(n) };
  const items = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.n != null) {
      const next = tokens[i + 1];
      const unitNext = next && next.w && /^(box|boxes|dabba|dabbe|dibba|pcs|piece|pieces|dozen|doz|packet|packets)$/.test(next.w);
      if (colSet.has(t.n) && !unitNext) items.push({ t: 'S', v: t.n });
      else if (t.n > 0 && t.n < 1000) items.push({ t: 'Q', v: t.n });
    } else if (RANGE_WORDS.has(t.w)) items.push({ t: 'R' });
    else if (EACH_WORDS.has(t.w)) items.push({ t: 'E' });
  }
  // 2. expand ranges S R S
  const flat = [];
  let each = false;
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    if (it.t === 'E') { each = true; continue; }
    if (it.t === 'R') {
      const prev = flat[flat.length - 1];
      const nx = items[i + 1];
      if (prev && prev.t === 'S' && nx && nx.t === 'S' && nx.v > prev.v) {
        flat.pop();
        for (const s of expandRange(prev.v, nx.v, cols)) flat.push({ t: 'S', v: s, fromRange: true });
        i++;
      }
      continue;
    }
    flat.push(it);
  }
  // 3. group consecutive kinds
  const groups = [];
  for (const it of flat) {
    const g = groups[groups.length - 1];
    if (g && g.t === it.t) g.v.push(it.v);
    else groups.push({ t: it.t, v: [it.v] });
  }
  // 4. pair sizes with quantities
  const qty = {};
  let lastQ = null;
  let leadingQ = null;
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i];
    const nx = groups[i + 1];
    if (g.t === 'S') {
      if (nx && nx.t === 'Q') {
        if (nx.v.length === g.v.length) g.v.forEach((s, k) => { qty[s] = nx.v[k]; });
        else if (nx.v.length === 1) g.v.forEach((s) => { qty[s] = nx.v[0]; });
        // Hindi reduplication: "60 se 85 tak char char" = 4 in every size
        else if (nx.v.every((q) => q === nx.v[0])) g.v.forEach((s) => { qty[s] = nx.v[0]; });
        else if (g.v.length === 1) {
          qty[g.v[0]] = nx.v[0];
          warnings.push(`Extra numbers ignored after size ${g.v[0]}: ${nx.v.slice(1).join(', ')}`);
        } else {
          const k = Math.min(g.v.length, nx.v.length);
          for (let j = 0; j < k; j++) qty[g.v[j]] = nx.v[j];
          warnings.push(`Sizes ${g.v.join(', ')} and quantities ${nx.v.join(', ')} do not line up - please check`);
        }
        lastQ = nx.v[nx.v.length - 1];
        i++;
      } else if (leadingQ != null) {
        g.v.forEach((s) => { qty[s] = leadingQ; });
        leadingQ = null;
      } else if (lastQ != null && each) {
        g.v.forEach((s) => { qty[s] = lastQ; });
      } else {
        warnings.push(`No quantity heard for size ${g.v.join(', ')}`);
      }
    } else if (g.t === 'Q') {
      if (nx && nx.t === 'S' && g.v.length === 1) {
        const after = groups[i + 2];
        if (!after || after.t !== 'Q') {
          nx.v.forEach((s) => { qty[s] = g.v[0]; });
          lastQ = g.v[0];
          i++;
          continue;
        }
      }
      if (i === 0 && g.v.length === 1) leadingQ = g.v[0];
    }
  }
  return { qty, warnings };
}

// ---------------------------------------------------------------------------
// Customer / transport detection
// ---------------------------------------------------------------------------

const titleCase = (s) => s.replace(/\b[a-z]/g, (c) => c.toUpperCase()).trim();

// Party name spoken in Hindi: keep it in Devanagari (transliterating names
// mangles them); the form's handwriting font renders Devanagari.
function detectHindiHeader(text, found) {
  const src = cleanDevanagari(text);
  const out = {};
  const m = src.match(/(?:पार्टी|ग्राहक|कस्टमर|दुकान|फर्म)(?:\s+का)?(?:\s+नाम)?(?:\s+है)?\s*[:\-]?\s+([^,।.!?\n]+)/);
  if (!m) return out;
  let words = m[1].trim().split(/\s+/);
  const stop = words.findIndex((w) => /^(है|हैं|जी|भाई|ऑर्डर|आर्डर|का|की|के|और)$/.test(w));
  if (stop >= 0) words = words.slice(0, stop);
  const se = words.findIndex((w) => /^(से|वाले|वाला|वाली)$/.test(w));
  if (se >= 1) {
    out.place = words[se - 1];
    words = words.slice(0, se - 1);
  }
  if (!out.place) {
    // "... मनोज टेक्सटाइल्स है, सिबसागर से"
    const pm = src.slice(m.index + m[0].length).match(/^\s*,?\s*([^\s,।.!?]+)\s+(?:से|वाले|वाला|वाली)(?=\s|$|[,।.!?])/);
    if (pm) out.place = pm[1];
  }
  words = words.filter((w) => /[\u0900-\u097F]/.test(w)).slice(0, 5);
  if (words.length) out.name = words.join(' ');
  else if (found.name) out.name = found.name;
  return out;
}

export function detectHeader(text) {
  const src = devanagariToRoman(text).toLowerCase();
  const out = {};
  const m = src.match(/\b(?:party(?:\s+ka)?(?:\s+(?:name|naam|nam))?|customer(?:\s+ka)?(?:\s+(?:name|naam))?|dukaan(?:\s+ka)?(?:\s+(?:name|naam))?|client(?:\s+name)?|m\s*\/\s*s|messrs|order\s+for|order\s+from)\s*(?:is|hai|:|-)?\s+([^.,;!?\n]+)/);
  if (m) {
    let words = m[1].replace(/[^a-z ]+/g, ' ').trim().split(/\s+/);
    const stop = words.findIndex((w) => /^(order|and|aur|ka|ke|ki|hai|is|haan|ok|okay|bhai|ji|sir)$/.test(w));
    if (stop >= 0) words = words.slice(0, stop);
    const hiAt = words.findIndex((w) => /^(se|wale|wala|wali)$/.test(w));
    const at = words.findIndex((w) => /^(from|of|at|in)$/.test(w));
    if (hiAt >= 1 && (at < 0 || hiAt < at)) {
      // Hindi order: "Manoj Textiles Sibsagar se" -> place is the word before "se"
      out.place = titleCase(words[hiAt - 1]);
      words = words.slice(0, hiAt - 1);
    } else if (at >= 0) {
      const place = words.slice(at + 1, at + 3);
      if (place.length) out.place = titleCase(place.join(' '));
      words = words.slice(0, at);
    }
    if (!out.place) {
      // "... Handloom Store hai, Tinsukia se"
      const pm = src.slice(m.index + m[0].length).match(/^\s*,?\s*([a-z]+)\s+(?:se|wale|wala|wali)\b/);
      if (pm) out.place = titleCase(pm[1]);
    }
    words = words.slice(0, 5);
    if (words.length) out.name = titleCase(words.join(' '));
  }
  if (hasDevanagari(text)) Object.assign(out, detectHindiHeader(text, out));
  const tr = src.match(/\b(?:through|by|via)\s+([a-z]+(?:\s+[a-z]+)?)\s+(transport|roadways|logistics|carriers?|cargo)\b/);
  if (tr) out.transport = titleCase(`${tr[1]} ${tr[2]}`);
  return out;
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

function sectionFor(product, sizes) {
  if (product && product.section === 'kids' && sizes.every((s) => KIDS_SIZES.includes(s))) return 'kids';
  if (sizes.some((s) => s < 75 || s === 73)) return 'kids';
  if (!product && sizes.length && sizes.every((s) => KIDS_SIZES.includes(s)) && sizes.some((s) => s < 75)) return 'kids';
  return 'adult';
}

function colsFor(product, hintKids) {
  if (product) {
    const priced = Object.keys(product.prices).map(Number);
    if (product.section === 'kids' || priced.some((s) => s < 75)) {
      return [...new Set([...KIDS_SIZES, ...priced])].sort((a, b) => a - b);
    }
    return [...new Set([...ADULT_SIZES, ...priced])].sort((a, b) => a - b);
  }
  return hintKids ? KIDS_SIZES : [...new Set([...KIDS_SIZES, ...ADULT_SIZES])].sort((a, b) => a - b);
}

let lineSeq = 0;
export const newLineId = () => `L${Date.now().toString(36)}${(lineSeq++).toString(36)}`;

/**
 * Parse a conversation transcript into an order draft.
 * @returns {{header: object, lines: Array, warnings: string[], ignored: string[]}}
 */
export function parseTranscript(text) {
  const tokens = tokenize(text);
  const segs = segment(tokens);
  const lines = [];
  const warnings = [];
  const ignored = [];
  const ctx = { lastBrand: null, lastCustom: null };

  for (const seg of segs) {
    const nameWords = seg.name.filter((t) => t.w).map((t) => t.w);
    const segText = [...seg.name, ...seg.nums].map((t) => (t.n != null ? t.n : t.w)).join(' ');
    const cancel = nameWords.some((w) => CANCEL_WORDS.has(w));
    const shapeInfo = extractShape([...seg.name, ...seg.nums]);

    // --- which product?
    let product = null;
    let custom = null;
    const codeTok = seg.name.find((t) => t.code);
    const codeNum = seg.name.find((t) => t.codeNum);
    if (codeTok || (codeNum && codeNum.bare)) {
      const prefix = codeTok ? codeTok.w.toUpperCase() : (ctx.lastCustom ? ctx.lastCustom.prefix : '');
      const num = codeNum ? String(codeNum.n) : '';
      custom = { prefix, code: [prefix, num].filter(Boolean).join(' ') };
    } else {
      const words = nameWords.filter((w) => !CANCEL_WORDS.has(w) && !SHAPE_WORDS.has(w) && !SAME_WORDS.has(w));
      const nameNums = seg.name.filter((t) => t.n != null).map((t) => String(t.n));
      const res = resolveProduct([...words, ...nameNums], ctx);
      if (res) product = res.product;
    }

    if (!product && !custom) {
      if (seg.nums.some((t) => t.n != null)) ignored.push(segText);
      continue;
    }

    const cols = colsFor(product, custom && ctx.lastCustom && ctx.lastCustom.kids);
    const numTokens = shapeInfo.rest.filter((t) => seg.nums.includes(t));
    const { qty, warnings: qw } = readQuantities(numTokens, cols);
    const sizes = Object.keys(qty).map(Number);

    const matchLine = (l) => (product ? l.productId === product.id : l.custom && l.desc === custom.code);

    if (cancel) {
      const idx = lines.findIndex(matchLine);
      if (idx >= 0) {
        if (sizes.length) for (const s of sizes) delete lines[idx].qty[s];
        else lines.splice(idx, 1);
      }
      continue;
    }
    if (!sizes.length) {
      if (seg.nums.length) ignored.push(segText);
      continue;
    }
    for (const w of qw) warnings.push(`${product ? product.short : custom.code}: ${w}`);

    let shape = product ? product.shape : '';
    if (custom) {
      shape = shapeInfo.shape || (ctx.lastCustom ? ctx.lastCustom.shape : '') || '';
      const known = CUSTOM.find((c) => c.key === norm(custom.code).replace(/ /g, ''));
      if (known && !shapeInfo.shape) shape = known.shape || shape;
    }

    const existing = lines.find(matchLine);
    if (existing) {
      Object.assign(existing.qty, qty);
      existing.section = sectionFor(product, Object.keys(existing.qty).map(Number));
    } else {
      const known = custom && CUSTOM.find((c) => c.key === norm(custom.code).replace(/ /g, ''));
      lines.push({
        id: newLineId(),
        productId: product ? product.id : null,
        desc: product ? product.short : custom.code,
        shape,
        section: sectionFor(product, sizes),
        qty,
        custom: !!custom,
        customRate: known ? Number(known.rate) || null : null,
      });
    }

    if (product) {
      ctx.lastBrand = INDEX.entries.find((e) => e.product === product).brand || ctx.lastBrand;
    } else {
      ctx.lastCustom = { prefix: custom.prefix, shape, kids: sizes.some((s) => s < 75) };
    }
  }

  return { header: detectHeader(text), lines, warnings, ignored };
}

export function productLabel(id) {
  const p = CATALOG_BY_ID[id];
  return p ? `${p.short} ${p.shape}`.trim() : '';
}
