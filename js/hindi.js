// Hindi support: converts Devanagari speech-to-text output (what Chrome
// returns when listening in Hindi, e.g. "रूबी आईडब्ल्यूडी पचासी में दो") into
// the romanised Hinglish the rule parser understands
// ("ruby iwd 85 mein do"). Latin text passes through untouched, so mixed
// transcripts ("Ruby IWD 85 में 2") work too.

// ---------------------------------------------------------------------------
// Unicode clean-up: nukta forms and chandrabindu vary between engines.
// ---------------------------------------------------------------------------
const NUKTA_PRECOMPOSED = {
  'क़': 'क', 'ख़': 'ख', 'ग़': 'ग', 'ज़': 'ज',
  'ड़': 'ड', 'ढ़': 'ढ', 'फ़': 'फ', 'य़': 'य',
};
export function cleanDevanagari(s) {
  return String(s || '')
    .replace(/[क़-य़]/g, (c) => NUKTA_PRECOMPOSED[c])
    .replace(/़/g, '')          // nukta
    .replace(/ँ/g, 'ं')    // chandrabindu -> anusvara
    .replace(/‌|‍/g, '');  // zero-width joiners
}

// ---------------------------------------------------------------------------
// Hindi numbers 1-100 (standard spellings + common variants)
// ---------------------------------------------------------------------------
const HI_NUM_LIST = [
  null, 'एक', 'दो', 'तीन', 'चार', 'पांच', 'छह', 'सात', 'आठ', 'नौ', 'दस',
  'ग्यारह', 'बारह', 'तेरह', 'चौदह', 'पंद्रह', 'सोलह', 'सत्रह', 'अठारह', 'उन्नीस', 'बीस',
  'इक्कीस', 'बाईस', 'तेईस', 'चौबीस', 'पच्चीस', 'छब्बीस', 'सत्ताईस', 'अट्ठाईस', 'उनतीस', 'तीस',
  'इकतीस', 'बत्तीस', 'तैंतीस', 'चौंतीस', 'पैंतीस', 'छत्तीस', 'सैंतीस', 'अड़तीस', 'उनतालीस', 'चालीस',
  'इकतालीस', 'बयालीस', 'तैंतालीस', 'चवालीस', 'पैंतालीस', 'छियालीस', 'सैंतालीस', 'अड़तालीस', 'उनचास', 'पचास',
  'इक्यावन', 'बावन', 'तिरेपन', 'चौवन', 'पचपन', 'छप्पन', 'सत्तावन', 'अट्ठावन', 'उनसठ', 'साठ',
  'इकसठ', 'बासठ', 'तिरसठ', 'चौंसठ', 'पैंसठ', 'छियासठ', 'सड़सठ', 'अड़सठ', 'उनहत्तर', 'सत्तर',
  'इकहत्तर', 'बहत्तर', 'तिहत्तर', 'चौहत्तर', 'पचहत्तर', 'छिहत्तर', 'सतहत्तर', 'अठहत्तर', 'उन्यासी', 'अस्सी',
  'इक्यासी', 'बयासी', 'तिरासी', 'चौरासी', 'पचासी', 'छियासी', 'सत्तासी', 'अट्ठासी', 'नवासी', 'नब्बे',
  'इक्यानवे', 'बानवे', 'तिरानवे', 'चौरानवे', 'पचानवे', 'छियानवे', 'सत्तानवे', 'अट्ठानवे', 'निन्यानवे', 'सौ',
];
const HI_NUM_VARIANTS = {
  'पाँच': 5, 'पाच': 5, 'छः': 6, 'छै': 6, 'छे': 6, 'छेह': 6, 'नो': 9, 'ग्यारा': 11, 'बारा': 12, 'पन्द्रह': 15,
  'पंदरह': 15, 'सतरह': 17, 'अट्ठारह': 18, 'उन्निस': 19, 'पच्चिस': 25, 'पैतीस': 35, 'पैंतिस': 35,
  'चालिस': 40, 'पैंतालिस': 45, 'पैतालीस': 45, 'पैंतालीस': 45, 'पचपण': 55, 'पैसठ': 65, 'पैंसट': 65,
  'तिहत्तर': 73, 'तेहत्तर': 73, 'पिचहत्तर': 75, 'पचत्तर': 75, 'पचहतर': 75, 'पिचत्तर': 75,
  'पिचासी': 85, 'पच्चासी': 85, 'पचाशी': 85, 'नब्बें': 90, 'नब्बा': 90, 'पंचानवे': 95, 'पचानबे': 95,
  'पिचानवे': 95, 'पचानव': 95, 'पचान्नवे': 95, 'पंचानबे': 95,
};
export const DEVANAGARI_NUMBERS = new Map();
HI_NUM_LIST.forEach((w, i) => { if (w) DEVANAGARI_NUMBERS.set(cleanDevanagari(w), i); });
for (const [w, n] of Object.entries(HI_NUM_VARIANTS)) DEVANAGARI_NUMBERS.set(cleanDevanagari(w), n);

// Romanised Hindi numbers (what English-India recognition, or a typed
// transcript, may contain). "do" (2) is handled separately in the parser
// because it is also the English verb.
export const ROMAN_HINDI_NUMBERS = {
  ek: 1, teen: 3, tin: 3, char: 4, chaar: 4, paanch: 5, panch: 5, paach: 5, panc: 5,
  chhe: 6, chhah: 6, chah: 6, chhai: 6, che: 6, cheh: 6, saat: 7, sat: 7, aath: 8, ath: 8, nau: 9,
  das: 10, gyarah: 11, gyara: 11, barah: 12, bara: 12, baara: 12, terah: 13, tera: 13, chaudah: 14, chauda: 14,
  pandrah: 15, pandra: 15, solah: 16, sola: 16, satrah: 17, satra: 17, atharah: 18, athara: 18,
  unnis: 19, bees: 20, bis: 20, ikkis: 21, bais: 22, teis: 23, chaubis: 24, pachchis: 25, pachis: 25,
  chhabbis: 26, sattais: 27, atthais: 28, untis: 29, tees: 30, tis: 30,
  paintis: 35, paintees: 35, pentis: 35, chalis: 40, chaalis: 40, chalees: 40,
  paintalis: 45, paintaalis: 45, pentalis: 45, pachas: 50, pachaas: 50, pachpan: 55,
  saath: 60, sath: 60, painsath: 65, pensath: 65, painsat: 65, sattar: 70, satar: 70,
  tihattar: 73, tehattar: 73, pachattar: 75, pachhattar: 75, pichattar: 75, pachhatar: 75,
  assi: 80, asi: 80, pachasi: 85, pachaasi: 85, pichasi: 85, pachchasi: 85,
  nabbe: 90, nabe: 90, navve: 90, pachanve: 95, pachaanve: 95, pachanbe: 95, panchanve: 95, pichanve: 95,
};

// ---------------------------------------------------------------------------
// Whole-word dictionary (product vocabulary and order-talk words)
// ---------------------------------------------------------------------------
const WORDS = {
  // brands
  'रूबी': 'ruby', 'रुबी': 'ruby', 'रूबि': 'ruby', 'लाइट': 'lite', 'लाईट': 'lite', 'लाइफ': 'lite',
  'क्लासिक': 'classic', 'क्लासिक्स': 'classic', 'इजी': 'ezee', 'ईजी': 'ezee', 'इज़ी': 'ezee', 'ईज़ी': 'ezee', 'इजि': 'ezee',
  'इजीलाइन': 'ezeeline', 'ईजीलाइन': 'ezeeline', 'एडवांस': 'advans', 'एडवान्स': 'advans', 'एडवांस्ड': 'advans',
  'चुनमुन': 'chunmun', 'जेंटीज': 'genteez', 'जेंटीस': 'genteez', 'जेंटिज': 'genteez', 'जेंट्स': 'genteez',
  'मार्कोस': 'marcos', 'मारकोस': 'marcos', 'मार्कस': 'marcos', 'मार्को': 'marcos', 'मार्कोज': 'marcos',
  'नटखट': 'natkhat', 'हनी': 'honey', 'रेस': 'race', 'लवली': 'lovely', 'सोफिया': 'sofiyaa', 'सोफ़िया': 'sofiyaa',
  'सूफिया': 'sofiyaa', 'करीना': 'karina', 'लिली': 'lily', 'हॉट': 'hot', 'हाट': 'hot', 'चेनलॉक': 'chainlock',
  'चैनलॉक': 'chainlock', 'सुपरफाइन': 'superfine', 'पार्कर': 'parker', 'इंटरलॉक': 'interlock', 'इंटरलोक': 'interlock',
  // garments
  'बनियान': 'vest', 'बनियाइन': 'vest', 'बनियन': 'vest', 'वेस्ट': 'vest', 'बनियानें': 'vest',
  'ड्रॉअर': 'drawer', 'ड्रावर': 'drawer', 'ड्रॉवर': 'drawer', 'ड्रॉर': 'drawer', 'ड्रोअर': 'drawer', 'ड्रायर': 'drawer',
  'चड्डी': 'drawer', 'चड्ढी': 'drawer', 'कच्छा': 'drawer', 'अंडरवियर': 'drawer',
  'ब्रीफ': 'brief', 'ब्रिफ': 'brief', 'ट्रंक': 'trunk', 'ट्रंक्स': 'trunk', 'जॉकी': 'jokee', 'जोकी': 'jokee', 'जॉकि': 'jokee',
  'बॉक्सर': 'boxer', 'बाक्सर': 'boxer', 'बरमूडा': 'bermuda', 'बर्मूडा': 'bermuda', 'पेंटी': 'penteez', 'पैंटी': 'penteez',
  'पेंटीज': 'penteez', 'पैंटीज': 'penteez', 'ब्लूमर': 'bloomer', 'समीज': 'sameez', 'शमीज': 'sameez', 'शमीज़': 'sameez',
  'शॉर्ट्स': 'shorts', 'निक्कर': 'shorts', 'जिम': 'gym', 'टीशर्ट': 'tshirt', 'स्पोर्टो': 'sporto',
  // descriptors
  'कलर': 'color', 'कलर्ड': 'color', 'रंगीन': 'color', 'कलरफुल': 'color', 'व्हाइट': 'white', 'वाइट': 'white', 'सफेद': 'white',
  'ग्रे': 'grey', 'ब्लैक': 'black', 'काला': 'black', 'काली': 'black', 'प्रिंट': 'print', 'प्रिंटेड': 'print', 'छपाई': 'print',
  'प्लेन': 'plain', 'सादा': 'plain', 'पॉकेट': 'pocket', 'पॉकिट': 'pocket', 'जेब': 'pocket', 'जेबवाला': 'pocket',
  'लॉन्ग': 'long', 'लांग': 'long', 'लंबा': 'long', 'मिनी': 'mini', 'मिड': 'mid', 'लाइक्रा': 'lycra', 'स्ट्रेच': 'lycra',
  'फोल्डिंग': 'folding', 'रिब': 'rib', 'फ्रेंची': 'frenchy', 'फ्रंट': 'front', 'ओपन': 'open', 'हाईकट': 'hicut',
  'इनर': 'inner', 'आउटर': 'outer', 'इलास्टिक': 'elastic', 'राउंड': 'round', 'नेक': 'neck', 'हाफ': 'half',
  'स्लीव': 'sleeve', 'स्लीव्स': 'sleeve', 'बाजू': 'sleeve', 'बाँह': 'sleeve', 'बांह': 'sleeve', 'गर्ल्स': 'girls',
  'प्रीमियम': 'premium', 'सुपर': 'super', 'डिजाइन': 'design', 'डिज़ाइन': 'design',
  // connectors
  'में': 'mein', 'मे': 'mein', 'मैं': 'mein', 'और': 'aur', 'व': 'aur', 'से': 'se', 'तक': 'tak', 'का': 'ka', 'के': 'ke', 'की': 'ki',
  'हर': 'har', 'सब': 'sab', 'सबमें': 'sabme', 'सभी': 'sabhi', 'सारे': 'saare', 'प्रत्येक': 'each', 'एक-एक': 'each',
  'डिब्बा': 'dabba', 'डिब्बे': 'dabbe', 'डब्बा': 'dabba', 'डब्बे': 'dabbe', 'बॉक्स': 'box', 'बाक्स': 'box', 'पेटी': 'box',
  'पीस': 'piece', 'दर्जन': 'dozen', 'दरजन': 'dozen', 'साइज': 'size', 'साईज': 'size', 'नंबर': 'number',
  'वाला': 'wala', 'वाली': 'wali', 'वाले': 'wale', 'भी': 'bhi', 'फिर': 'phir', 'उसके': 'next', 'बाद': 'baad',
  'नहीं': 'nahi', 'नही': 'nahi', 'मत': 'mat', 'हटाओ': 'hatao', 'हटा': 'hata', 'हटा दो': 'hatao', 'कैंसिल': 'cancel',
  'कैंसल': 'cancel', 'रद्द': 'cancel', 'काटो': 'cut', 'काट': 'cut',
  'वही': 'wahi', 'वैसा': 'same', 'वैसे': 'same', 'सेम': 'same', 'डिट्टो': 'ditto', 'दित्तो': 'ditto', 'उसी': 'usi',
  'नेट': 'net', 'परसेंट': 'percent', 'प्रतिशत': 'percent', 'पर्सेंट': 'percent', 'कम': 'less', 'माइनस': 'minus',
  'लेस': 'less', 'होलसेल': 'wholesale', 'डिस्काउंट': 'discount', 'छूट': 'discount',
  'पार्टी': 'party', 'नाम': 'naam', 'ग्राहक': 'customer', 'कस्टमर': 'customer', 'दुकान': 'dukaan', 'फर्म': 'firm',
  'ट्रांसपोर्ट': 'transport', 'रोडवेज': 'roadways', 'रोडवेज़': 'roadways', 'कार्गो': 'cargo', 'लॉजिस्टिक्स': 'logistics',
  'थ्रू': 'through', 'द्वारा': 'through', 'ऑर्डर': 'order', 'आर्डर': 'order', 'रेट': 'rate', 'भाव': 'rate',
  'क्या': 'kya', 'है': 'hai', 'हैं': 'hai', 'दे': 'de', 'दीजिए': 'dijiye', 'दीजिये': 'dijiye', 'चाहिए': 'chahiye',
  'लिखो': 'likho', 'लिख': 'likh', 'भेजो': 'bhejo', 'भेज': 'bhej', 'जी': 'ji', 'हां': 'haan', 'हाँ': 'haan', 'ठीक': 'theek',
  'सर': 'sir', 'भाई': 'bhai', 'भैया': 'bhaiya', 'टू': 'to', 'फॉर': 'for', 'एंड': 'and', 'ईच': 'each',
  'दो': 'do', 'सौ': 'hundred', 'हंड्रेड': 'hundred',
  // English words said in Hindi, so the transcript reads naturally in Roman
  'टेक्सटाइल्स': 'textiles', 'टेक्सटाइल': 'textile', 'स्टोर': 'store', 'स्टोर्स': 'stores', 'ट्रेडर्स': 'traders',
  'एंटरप्राइजेज': 'enterprises', 'एंटरप्राइज': 'enterprise', 'गारमेंट्स': 'garments', 'होजरी': 'hosiery', 'हौजरी': 'hosiery',
  'हैंडलूम': 'handloom', 'फैशन': 'fashion', 'कलेक्शन': 'collection', 'सेंटर': 'centre', 'ब्रदर्स': 'brothers', 'संस': 'sons',
  'हाउस': 'house', 'एम्पोरियम': 'emporium', 'मार्ट': 'mart', 'बाजार': 'bazaar', 'बाज़ार': 'bazaar',
  'स्कीम': 'scheme', 'फ्री': 'free', 'ऑफर': 'offer', 'पेमेंट': 'payment', 'बिल': 'bill', 'स्टॉक': 'stock',
  'डिलीवरी': 'delivery', 'चार्ट': 'chart', 'कैटलॉग': 'catalogue', 'सैंपल': 'sample', 'क्वालिटी': 'quality',
  'वैरायटी': 'variety', 'स्टाइल': 'style', 'पैकिंग': 'packing', 'जार': 'jar', 'मीडियम': 'medium', 'स्मॉल': 'small',
  'लार्ज': 'large', 'डॉक्टर': 'doctor', 'टाइम': 'time', 'मोबाइल': 'mobile', 'फोन': 'phone', 'ओके': 'ok',
  'थैंक्यू': 'thank you', 'सॉरी': 'sorry', 'प्लीज': 'please', 'सेल': 'sale', 'कंपनी': 'company', 'मार्केट': 'market',
  'आप': 'aap', 'आपका': 'aapka', 'आपको': 'aapko', 'आना': 'aana', 'जाना': 'jaana', 'काम': 'kaam', 'दाम': 'daam',
  'माल': 'maal', 'साल': 'saal', 'आज': 'aaj', 'बाकी': 'baaki', 'बात': 'baat', 'ज़रूर': 'zaroor', 'जरूर': 'zaroor',
};
const WORD_MAP = new Map(Object.entries(WORDS).map(([k, v]) => [cleanDevanagari(k), v]));

// English letters as spoken in Hindi: "आई डब्ल्यू डी" -> i w d
const LETTERS = {
  'ए': 'a', 'बी': 'b', 'सी': 'c', 'डी': 'd', 'ई': 'e', 'एफ': 'f', 'जी': 'g', 'एच': 'h', 'आई': 'i', 'जे': 'j',
  'के': 'k', 'एल': 'l', 'एम': 'm', 'एन': 'n', 'ओ': 'o', 'पी': 'p', 'क्यू': 'q', 'आर': 'r', 'एस': 's', 'टी': 't',
  'यू': 'u', 'वी': 'v', 'डब्ल्यू': 'w', 'डबल्यू': 'w', 'डब्लू': 'w', 'डबलू': 'w', 'एक्स': 'x', 'वाई': 'y', 'जेड': 'z', 'ज़ेड': 'z',
};
const LETTER_MAP = new Map(Object.entries(LETTERS).map(([k, v]) => [cleanDevanagari(k), v]));
// Standalone words that are ordinary Hindi, not letters.
const NOT_LETTER_ALONE = new Set(['के', 'जी'].map(cleanDevanagari));

// Split a word made only of letter names ("आईसीडी" -> "icd").
function lettersOf(word) {
  const n = word.length;
  const best = new Array(n + 1).fill(null);
  best[0] = '';
  for (let i = 0; i < n; i++) {
    if (best[i] == null) continue;
    for (const [name, letter] of LETTER_MAP) {
      if (word.startsWith(name, i) && best[i + name.length] == null) best[i + name.length] = best[i] + letter;
    }
  }
  return best[n] && best[n].length >= 2 ? best[n].toUpperCase() : null;
}

// ---------------------------------------------------------------------------
// Fallback phonetic transliteration with Hindi schwa deletion
// ---------------------------------------------------------------------------
const CONS = {
  'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'n', 'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'n',
  'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n', 'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
  'प': 'p', 'फ': 'f', 'ब': 'b', 'भ': 'bh', 'म': 'm', 'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'श': 'sh',
  'ष': 'sh', 'स': 's', 'ह': 'h', 'ळ': 'l',
};
const VOWELS = {
  'अ': 'a', 'आ': 'a', 'इ': 'i', 'ई': 'i', 'उ': 'u', 'ऊ': 'u', 'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au',
  'ऑ': 'o', 'ऍ': 'e',
};
const MATRAS = {
  'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u', 'ृ': 'ri', 'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ॉ': 'o', 'ॅ': 'e',
};
const VIRAMA = '्';

export function transliterate(word) {
  // Build syllables: {c: consonant roman, v: vowel roman | 'a' (inherent) | '' (virama), tail: 'n'/'h'}
  const syl = [];
  for (let i = 0; i < word.length; i++) {
    const ch = word[i];
    if (CONS[ch]) {
      const next = word[i + 1];
      if (next === VIRAMA) { syl.push({ c: CONS[ch], v: '', inherent: false }); i++; }
      else if (next && MATRAS[next]) { syl.push({ c: CONS[ch], v: MATRAS[next], inherent: false }); i++; }
      else syl.push({ c: CONS[ch], v: 'a', inherent: true });
    } else if (VOWELS[ch]) syl.push({ c: '', v: VOWELS[ch], inherent: false });
    else if (ch === 'ं') { if (syl.length) syl[syl.length - 1].tail = 'n'; }
    else if (ch === 'ः') { if (syl.length) syl[syl.length - 1].tail = 'h'; }
    else if (MATRAS[ch]) syl.push({ c: '', v: MATRAS[ch], inherent: false });
    else if (/[0-9a-z]/i.test(ch)) syl.push({ c: ch.toLowerCase(), v: '', inherent: false });
  }
  // Schwa deletion, right to left: drop the final inherent "a", and a medial
  // one when the syllables on both sides have vowels (नटखट -> natkhat).
  const hasV = (s) => s && s.v !== '';
  for (let i = syl.length - 1; i >= 0; i--) {
    const s = syl[i];
    if (!s.inherent || s.tail) continue;
    if (i === syl.length - 1) { if (i > 0) s.v = ''; continue; }
    if (i > 0 && hasV(syl[i - 1]) && hasV(syl[i + 1]) && syl[i + 1].c) s.v = '';
  }
  return syl.map((s) => s.c + s.v + (s.tail || '')).join('');
}

// ---------------------------------------------------------------------------
// Public: Devanagari text -> romanised text
// ---------------------------------------------------------------------------
const DEVANAGARI_WORD = /[ऀ-ॿ]+/g;

// For showing the transcript in English letters: "रूबी आईडब्ल्यूडी पचासी में दो"
// -> "Ruby IWD 85 mein do". Line breaks are kept.
export function toRomanScript(text) {
  if (!hasDevanagari(text)) return String(text || '');
  return String(text).split('\n').map((line) => {
    const r = devanagariToRoman(line)
      .replace(/[ \t]+/g, ' ').replace(/ ([,.?!])/g, '$1').replace(/([.?!])\1+/g, '$1').trim();
    return r.charAt(0).toUpperCase() + r.slice(1);
  }).join('\n');
}

export function hasDevanagari(text) {
  return /[ऀ-ॿ]/.test(String(text || ''));
}

export function devanagariToRoman(text) {
  let s = cleanDevanagari(text);
  if (!hasDevanagari(s)) return s;
  s = s.replace(/[०-९]/g, (d) => String(d.charCodeAt(0) - 0x966)).replace(/।|॥/g, '. ');
  // "एक सौ दस" -> 110, bare "सौ" -> 100
  const one = cleanDevanagari('एक');
  s = s.replace(new RegExp(`(?:(${one})\\s+)?${cleanDevanagari('सौ')}(?:\\s+([\\u0900-\\u097F]+))?`, 'g'), (m, ek, nxt) => {
    const v = nxt && DEVANAGARI_NUMBERS.get(nxt);
    if (ek && v && v <= 30) return ` ${100 + v} `;
    return ` 100 ${nxt || ''} `;
  });
  // multi-word phrases first
  s = s.replace(/हटा\s+दो/g, 'hatao').replace(/एक\s*-\s*एक/g, 'each').replace(/दे\s+दो/g, 'de dijiye');
  return s.replace(DEVANAGARI_WORD, (w) => {
    if (DEVANAGARI_NUMBERS.has(w) && !WORD_MAP.has(w)) return ` ${DEVANAGARI_NUMBERS.get(w)} `;
    if (WORD_MAP.has(w)) return ` ${WORD_MAP.get(w)} `;
    if (LETTER_MAP.has(w) && !NOT_LETTER_ALONE.has(w)) return ` ${LETTER_MAP.get(w).toUpperCase()} `;
    const letters = lettersOf(w);
    if (letters) return ` ${letters} `;
    return ` ${transliterate(w)} `;
  });
}
