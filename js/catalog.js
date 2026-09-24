// Product catalog transcribed from "Price List For NE" dated 15-05-2026
// (Skipper Hosiery Pvt. Ltd., North East only).
//
// Rates are per box of 10 pcs including GST, except items marked unit "doz"
// (Honey penteez and Sofiyaa ranges), which are per dozen.
//
// Each entry:
//   id       stable key used by the parser and the AI prompt
//   name     full name as printed on the price list
//   short    what goes in the DESCRIPTION column of the order form
//   shape    what goes in the SHAPE column (RN, RNS, O/E, I/E ...)
//   section  default form section: "adult" (sizes 75-130) or "kids" (30-85)
//   prices   { "80-85-90": 630 } -> every size in the key gets that rate
//   mrp      MRP per piece (informational)
//   aliases  extra spoken phrases that should resolve to this product

export const PRICE_LIST_DATE = '15-05-2026';

export const ADULT_SIZES = [75, 80, 85, 90, 95, 100, 105, 110, 115, 120, 125, 130];
export const KIDS_SIZES = [30, 35, 40, 45, 50, 55, 60, 65, 70, 73, 75, 80, 85];

// Brands - used for "ditto" handling: "Ruby IWD ... ICD" means Ruby ICD.
export const BRANDS = [
  'ezee', 'ezeeline', 'advans', 'ruby', 'classic', 'chunmun', 'genteez', 'hot',
  'marcos', 'natkhat', 'honey', 'race4', 'lovely', 'sofiyaa', 'lite', 'karina', 'lily',
];

const RAW = [
  // ---------------- GROUP A : VESTS ----------------
  ['ezee-wg-rn', 'EZEE WHITE & GREY (CHAINLOCK) RN', 'Ezee White/Grey', 'RN', 'adult',
    { '75': 590, '80-85-90': 630, '95-100': 730 }, 120, ['ezee grey', 'ezee chainlock']],
  ['ezee-wg-rns', 'EZEE WHITE & GREY (CHAINLOCK) RNS', 'Ezee White/Grey', 'RNS', 'adult',
    { '80-85-90': 780, '95-100': 880 }, 150, ['ezee grey rns', 'ezee chainlock rns']],
  ['ezee-wf-rn', 'EZEE WHITE FOLDING RN', 'Ezee White Folding', 'RN', 'adult',
    { '40-45-50': 330, '55-60-65': 370, '70': 410, '73': 450, '75': 600, '80-85-90': 640, '95-100': 740 }, 120,
    ['ezee white', 'ezee folding']],
  ['ezee-wf-rns', 'EZEE WHITE FOLDING RNS', 'Ezee White Folding', 'RNS', 'adult',
    { '55-60-65': 430, '70': 470, '73': 510, '75': 750, '80-85-90': 790, '95-100': 890 }, 150,
    ['ezee white rns', 'ezee folding rns']],
  ['ezee-cf-rn', 'EZEE COLOR FOLDING RN', 'Ezee Color Folding', 'RN', 'adult',
    { '40-45-50': 360, '55-60-65': 400, '70': 440, '73': 480, '75': 640, '80-85-90': 680, '95-100': 780 }, 130,
    ['ezee color vest']],
  ['ezee-cf-rns', 'EZEE COLOR FOLDING RNS', 'Ezee Color Folding', 'RNS', 'adult',
    { '80-85-90': 830, '95-100': 930 }, 160, ['ezee color vest rns']],
  ['chunmun-rn', 'CHUNMUN PRINT RN', 'Chunmun Print', 'RN', 'kids',
    { '40-45-50': 320, '55-60-65': 340, '70': 370, '73': 400 }, null, ['chunmun']],
  ['ezeeline-rn', 'EZEELINE WHITE FOLDING RN (PREMIUM VEST)', 'Ezeeline White Folding', 'RN', 'adult',
    { '75': 690, '80-85-90': 730, '95-100': 830, '105-110': 930 }, 150, ['ezeeline', 'premium vest']],
  ['ezeeline-rns', 'EZEELINE WHITE FOLDING RNS (PREMIUM VEST)', 'Ezeeline White Folding', 'RNS', 'adult',
    { '80-85-90': 880, '95-100': 980, '105-110': 1080 }, 180, ['ezeeline rns', 'premium vest rns']],
  ['advans-rn', 'ADVANS WHITE FOLDING RN (SUPER PREMIUM VEST)', 'Advans White Folding', 'RN', 'adult',
    { '75': 710, '80-85-90': 740, '95-100': 840, '105-110': 940 }, 160, ['advans', 'super premium vest']],
  ['advans-rns', 'ADVANS WHITE FOLDING RNS (SUPER PREMIUM VEST)', 'Advans White Folding', 'RNS', 'adult',
    { '80-85-90': 890, '95-100': 990, '105-110': 1090 }, 190, ['advans rns', 'super premium vest rns']],
  ['ruby-sf-rn', 'RUBY CHAINLOCK SUPERFINE RN', 'Ruby Superfine', 'RN', 'adult',
    { '80-85-90': 710, '95-100': 810 }, 140, ['ruby vest', 'ruby chainlock', 'ruby superfine']],
  ['ruby-sf-rns', 'RUBY CHAINLOCK SUPERFINE RNS', 'Ruby Superfine', 'RNS', 'adult',
    { '80-85-90': 860, '95-100': 960 }, 170, ['ruby vest rns', 'ruby superfine rns']],
  ['classic-pw-rn', 'CLASSIC PARKER WHITE RN', 'Classic Parker White', 'RN', 'adult',
    { '75': 750, '80-85-90': 800, '95-100': 900 }, 150, ['parker white', 'classic parker']],
  ['classic-pw-rns', 'CLASSIC PARKER WHITE RNS', 'Classic Parker White', 'RNS', 'adult',
    { '80-85-90': 950, '95-100': 1050 }, 180, ['parker white rns', 'classic parker rns']],
  ['classic-pb-rn', 'CLASSIC PARKER BLACK RN', 'Classic Parker Black', 'RN', 'adult',
    { '75': 800, '80-85-90': 850, '95-100': 950 }, 160, ['parker black']],
  ['classic-il-rn', 'CLASSIC INTERLOCK RN', 'Classic Interlock', 'RN', 'adult',
    { '80-85-90': 780, '95-100': 880 }, 150, ['interlock vest']],
  ['classic-il-rns', 'CLASSIC INTERLOCK RNS', 'Classic Interlock', 'RNS', 'adult',
    { '80-85-90': 930, '95-100': 1030 }, 180, ['interlock vest rns']],
  ['classic-ilp-rn', 'CLASSIC INTERLOCK POCKET RN', 'Classic Interlock Pocket', 'RN', 'adult',
    { '80-85-90': 860, '95-100': 960 }, 160, ['interlock pocket vest']],
  ['classic-ilp-rns', 'CLASSIC INTERLOCK POCKET RNS', 'Classic Interlock Pocket', 'RNS', 'adult',
    { '80-85-90': 1010, '95-100': 1110 }, 190, ['interlock pocket vest rns']],

  // ---------------- GROUP A : BRIEFS & TRUNKS ----------------
  ['ruby-icd', 'RUBY INT COLOR DRAWER ICD', 'Ruby ICD', 'O/E', 'adult',
    { '75': 840, '80-85-90': 880, '95-100': 980, '105-110': 1080, '115-120': 1180, '125-130': 1280 }, 160,
    ['ruby icd', 'ruby color drawer', 'ruby interlock color drawer']],
  ['ruby-iwd', 'INT WHITE DRAWER IWD', 'Ruby IWD', 'O/E', 'adult',
    { '80-85-90': 860, '95-100': 960, '105-110': 1060, '115-120': 1160, '125-130': 1260 }, 150,
    ['ruby iwd', 'iwd', 'white drawer', 'ruby white drawer']],
  ['ruby-icdp', 'INNER POCKET DRAWER ICDP', 'Ruby ICD Pocket', 'O/E', 'adult',
    { '80-85-90': 1080, '95-100': 1180 }, 200,
    ['icdp', 'icd pocket', 'ruby icd pocket', 'ruby pocket', 'inner pocket drawer']],
  ['ruby-rcd', 'RUBY RIB DRAWER RCD', 'Ruby RCD', 'O/E', 'adult',
    { '80-85-90': 900, '95-100': 1000 }, 160, ['rcd', 'ruby rib', 'rib drawer']],
  ['ezee-cd', 'EZEE COLOR / WHITE DRAWER CD', 'Ezee CD', 'O/E', 'adult',
    { '75': 730, '80-85-90': 770, '95-100': 850, '105-110': 930 }, 150, ['ezee drawer', 'ezee cd']],
  ['ezee-cbrief', 'EZEE COLOUR BRIEF', 'Ezee Col Brief', 'O/E', 'adult',
    { '75': 610, '80-85-90': 650, '95-100': 700 }, 120, ['ezee brief', 'ezee color brief']],
  ['ezee-jokee', 'EZEE FRONT OPEN JOKEE COL/WHITE', 'Ezee FO Jokee', 'O/E', 'adult',
    { '75': 610, '80-85-90': 650, '95-100': 700, '105-110': 750 }, 120, ['ezee jokee', 'ezee front open']],
  ['genteez-brief', 'GENTEEZ BRIEF (FRENCHY) COL', 'Genteez Brief', 'I/E', 'adult',
    { '75': 570, '80-85-90': 620, '95-100': 670 }, 120, ['genteez', 'frenchy']],
  ['genteez-jokee', 'GENTEEZ JOKEE (FRONT OPEN) COL/WHITE', 'Genteez Jokee', 'I/E', 'adult',
    { '75': 580, '80-85-90': 620, '95-100': 670 }, 120, ['genteez jokee', 'genteez front open']],
  ['classic-lt-ie', 'CLASSIC LONG TRUNK DRAWER COL/WHITE', 'Classic Long Trunk', 'I/E', 'adult',
    { '80-85-90': 800, '95-100': 900, '105-110': 1000 }, 160,
    ['classic trunk', 'classic long trunk', 'classic long trunk ie']],
  ['classic-lt-oe', 'CLASSIC LONG TRUNK DRAWER COL', 'Classic Col Long Trunk', 'O/E', 'adult',
    { '80-85-90': 830, '95-100': 930 }, 170,
    ['classic color trunk', 'classic long trunk oe', 'classic col trunk']],
  ['ezee-pbrief', 'EZEE PRINTED BRIEF', 'Ezee Printed Brief', 'I/E', 'adult',
    { '75': 720, '80-85-90': 750, '95-100': 830, '105-110': 910 }, 140, ['ezee print brief']],
  ['ezee-pdrawer', 'EZEE PRINTED DRAWER (BOX PACKING)', 'Ezee Printed Drawer', 'I/E', 'adult',
    { '75': 860, '80-85-90': 900, '95-100': 1000 }, 170, ['ezee print drawer']],
  ['ezee-plt', 'EZEE PRINTED LONG TRUNK', 'Ezee Printed Long Trunk', 'I/E', 'adult',
    { '80-85-90': 920, '95-100': 1020 }, 180, ['ezee print trunk', 'ezee printed trunk']],
  ['hot-hicut', 'HOT HI-CUT BRIEF', 'Hot Hi-Cut Brief', 'O/E', 'adult',
    { '80-85-90': 650, '95-100': 700 }, 130, ['hot brief', 'hicut brief', 'hot hicut']],
  ['marcos-cbrief', 'MARCOS COLOR BRIEF 502', 'Marcos Col Brief', 'O/E', 'adult',
    { '80-85-90': 720, '95-100': 770 }, 140, ['marcos brief', 'marcos 502', 'marcos color brief']],
  ['marcos-cmini', 'MARCOS COLOR MINI BOXER', 'Marcos Col Mini Boxer', 'O/E', 'adult',
    { '80-85-90': 820, '95-100': 900 }, 160, ['marcos mini boxer', 'marcos mini']],
  ['marcos-pbrief', 'MARCOS PRINTED BRIEF', 'Marcos Printed Brief', 'O/E', 'adult',
    { '80-85-90': 770, '95-100': 850 }, 150, ['marcos print brief']],
  ['marcos-pmini', 'MARCOS PRINTED MINI BOXER', 'Marcos Printed Mini Boxer', 'O/E', 'adult',
    { '80-85-90': 870, '95-100': 970 }, 170, ['marcos print mini']],
  ['marcos-pboxer', 'MARCOS PRINT BOXER', 'Marcos Print Boxer', 'I/E', 'adult',
    { '75': 850, '80-85-90': 900, '95-100': 1000 }, 180, ['marcos boxer']],
  ['marcos-lbrief', 'MARCOS STRETCH (LYCRA) BRIEF PLAIN', 'Marcos Lycra Brief', 'O/E', 'adult',
    { '80-85-90': 850, '95-100': 950 }, 175, ['marcos lycra brief', 'marcos stretch brief']],
  ['marcos-lmid', 'MARCOS STRETCH (LYCRA) MID TRUNK PLAIN', 'Marcos Lycra Mid Trunk', 'O/E', 'adult',
    { '80-85-90': 1030, '95-100': 1130 }, 205, ['marcos mid trunk', 'marcos lycra mid']],
  ['marcos-llong', 'MARCOS STRETCH (LYCRA) LONG TRUNK PLAIN', 'Marcos Lycra Long Trunk', 'O/E', 'adult',
    { '80-85-90': 1080, '95-100': 1230 }, 215, ['marcos long trunk', 'marcos lycra long']],
  ['marcos-pbermuda', 'MARCOS PLAIN BERMUDA', 'Marcos Plain Bermuda', 'I/E', 'adult',
    { '80-85-90': 1820, '95-100': 1970 }, 360, ['marcos bermuda', 'marcos plain bermuda']],
  ['marcos-prbermuda', 'MARCOS PRINTED BERMUDA', 'Marcos Printed Bermuda', 'I/E', 'adult',
    { '80-85-90': 2020, '95-100': 2170 }, 400, ['marcos print bermuda']],

  // ---------------- GROUP A : KIDS WEAR ----------------
  ['natkhat-rn', 'NATKHAT PRINT RN', 'Natkhat Print', 'RN', 'kids',
    { '35': 460, '40-45-50': 490, '55-60-65': 520, '70': 540, '73': 560, '75': 590 }, 105, ['natkhat vest', 'natkhat rn']],
  ['natkhat-rns', 'NATKHAT PRINT RNS (BASIC T-SHIRT)', 'Natkhat Print', 'RNS', 'kids',
    { '40-45-50': 940, '55-60-65': 970, '70': 1000, '75': 1050, '80-85': 1150 }, 220, ['natkhat t shirt', 'natkhat tshirt', 'natkhat rns']],
  ['natkhat-cj', 'NATKHAT FRONT OPEN O/E JOKEE CJ', 'Natkhat FO Jokee CJ', 'O/E', 'kids',
    { '40-45-50': 420, '55-60-65': 450, '70': 470, '73': 490, '75': 520 }, 90, ['natkhat jokee', 'natkhat cj']],
  ['natkhat-cd', 'NATKHAT FRONT OPEN O/E DRAWER CD', 'Natkhat FO Drawer CD', 'O/E', 'kids',
    { '40-45-50': 470, '55-60-65': 500, '70': 520, '73': 540, '75': 570 }, 105, ['natkhat cd', 'natkhat front open drawer']],
  ['natkhat-iebrief', 'NATKHAT I/E BRIEF', 'Natkhat Brief', 'I/E', 'kids',
    { '40-45-50': 400, '55-60-65': 430, '70': 450, '73': 470, '75': 500 }, 85, ['natkhat brief']],
  ['natkhat-iedrawer', 'NATKHAT I/E DRAWER', 'Natkhat Drawer', 'I/E', 'kids',
    { '40-45-50': 450, '55-60-65': 480, '70': 500, '73': 520, '75': 550 }, 95, ['natkhat drawer']],
  ['natkhat-stripe', 'NATKHAT STRIPE DRAWER I/E', 'Natkhat Stripe Drawer', 'I/E', 'kids',
    { '40-45-50': 440, '55-60-65': 470, '70': 490, '73': 510, '75': 540 }, 95, ['natkhat stripe']],
  ['natkhat-pbrief', 'NATKHAT PRINTED I/E BRIEF', 'Natkhat Printed Brief', 'I/E', 'kids',
    { '40-45-50': 460, '55-60-65': 490, '70': 510, '73': 540, '75': 570 }, 105, ['natkhat print brief']],
  ['natkhat-pdrawer', 'NATKHAT PRINTED I/E DRAWER', 'Natkhat Printed Drawer', 'I/E', 'kids',
    { '40-45-50': 550, '55-60-65': 580, '70': 600, '73': 630, '75': 660 }, 120, ['natkhat print drawer']],
  ['natkhat-hicut', 'NATKHAT HI-CUT O/E BRIEF', 'Natkhat Hi-Cut Brief', 'O/E', 'kids',
    { '40-45-50': 420, '55-60-65': 450, '70': 470, '73': 490 }, 95, ['natkhat hicut']],
  ['natkhat-icd', 'NATKHAT ICD', 'Natkhat ICD', 'O/E', 'kids',
    { '55-60-65': 530, '70': 550, '73': 580 }, 105, ['natkhat icd']],
  ['natkhat-pbermuda', 'NATKHAT PLAIN BERMUDA I/E', 'Natkhat Plain Bermuda', 'I/E', 'kids',
    { '40-45-50': 1010, '55-60-65': 1110, '70': 1160, '75': 1260, '80-85': 1360 }, 230, ['natkhat bermuda', 'natkhat plain bermuda']],
  ['natkhat-prbermuda', 'NATKHAT PRINTED BERMUDA I/E', 'Natkhat Printed Bermuda', 'I/E', 'kids',
    { '40-45-50': 1260, '55-60-65': 1360, '70': 1410, '75': 1510, '80-85': 1610 }, 275, ['natkhat print bermuda']],
  ['honey-ie', 'HONEY PLAIN I/E PENTEEZ (PER DOZEN)', 'Honey Plain Penteez', 'I/E', 'kids',
    { '40-45-50': 456, '55-60-65': 480, '70': 516, '73': 540, '75': 576 }, 85, ['honey penteez', 'honey plain', 'honey ie penteez'], 'doz'],
  ['honey-oe', 'HONEY PLAIN O/E PENTEEZ (PER DOZEN)', 'Honey Plain Penteez', 'O/E', 'kids',
    { '40-45-50': 468, '55-60-65': 492, '70': 528, '73': 552, '75': 588 }, 85, ['honey oe penteez', 'honey plain oe'], 'doz'],
  ['honey-sameez', 'HONEY GIRLS SAMEEZ', 'Honey Girls Sameez', '', 'kids',
    { '40-45-50': 450, '55-60-65': 480, '70': 500, '73': 520, '75': 550 }, 95, ['honey sameez', 'girls sameez']],
  ['honey-sporto', 'HONEY PRINT SPORTO RN', 'Honey Sporto', 'RN', 'kids',
    { '40-45-50': 550, '55-60-65': 580, '70': 600, '73': 620, '75': 670 }, 115, ['honey sporto', 'sporto']],
  ['honey-shorts', 'HONEY SHORTS', 'Honey Shorts', '', 'kids',
    { '40-45-50': 460, '55-60-65': 490, '70': 510, '73': 530, '75': 560 }, 105, ['honey shorts']],

  // ---------------- GROUP A : GYM VESTS ----------------
  ['race4-gym', 'RACE4 GYM VEST', 'Race4 Gym Vest', '', 'adult',
    { '45-50': 620, '55-60-65': 650, '70': 680, '73': 710, '75': 970, '80-85-90-95-100': 1070 }, 215, ['race4', 'race4 gym']],
  ['classic-gym', 'CLASSIC COLOR GYM VEST', 'Classic Gym Vest', '', 'adult',
    { '45-50': 560, '55-60-65': 590, '70': 620, '73': 650, '75': 780, '80-85-90-95-100': 880 }, 175,
    ['classic gym', 'classic gym vest', 'classic color gym']],

  // ---------------- GROUP A : PENTEEZ ----------------
  ['lovely-plain', 'LOVELY PLAIN I/E PENTEEZ (2PCS PACK)', 'Lovely Plain Penteez', 'I/E', 'adult',
    { '80-85-90': 480, '95-100': 480, '105-110': 530 }, 90, ['lovely plain', 'lovely']],
  ['lovely-print', 'LOVELY PRINTED I/E PENTEEZ (2PCS PACK)', 'Lovely Printed Penteez', 'I/E', 'adult',
    { '75': 470, '80-85-90': 500, '95-100': 500 }, 100, ['lovely print']],
  ['ezee-penteez', 'EZEE PRINTED I/E PENTEEZ (JAR PACKING)', 'Ezee Printed Penteez', 'I/E', 'adult',
    { '80-85-90': 640, '95-100': 640 }, 120, ['ezee penteez']],
  ['sofiyaa-1100', 'SOFIYAA 1100/1200 I/E PRINT PENTEEZ (ASSORTED)', 'Sofiyaa 1100', 'I/E', 'adult',
    { '75': 592, '80-85-90': 636, '95-100': 636, '105-110': 696 }, 100, ['sofiyaa 1100', 'sofiyaa 1200'], 'doz'],
  ['sofiyaa-1300', 'SOFIYAA 1300 O/E DEEP PRINT PENTEEZ', 'Sofiyaa 1300', 'O/E', 'adult',
    { '75': 618, '80-85-90': 648, '95-100': 648, '105-110': 708 }, 110, ['sofiyaa 1300'], 'doz'],
  ['sofiyaa-2100', 'SOFIYAA 2100 F/E DEEP PRINT MERCERISED PENTEEZ', 'Sofiyaa 2100', 'F/E', 'adult',
    { '75': 726, '80-85-90': 756, '95-100': 756, '105-110': 804 }, 120, ['sofiyaa 2100'], 'doz'],
  ['sofiyaa-2200', 'SOFIYAA 2200 I/E DEEP PRINT MERCERISED PENTEEZ', 'Sofiyaa 2200', 'I/E', 'adult',
    { '75': 714, '80-85-90': 744, '95-100': 744, '105-110': 804 }, 120, ['sofiyaa 2200'], 'doz'],
  ['sofiyaa-3100', 'SOFIYAA 3100 I/E PLAIN PENTEEZ', 'Sofiyaa 3100', 'I/E', 'adult',
    { '80-85-90': 588, '95-100': 588, '105-110': 648 }, 90, ['sofiyaa 3100'], 'doz'],
  ['sofiyaa-3200', 'SOFIYAA 3200 I/E PRINT PENTEEZ', 'Sofiyaa 3200', 'I/E', 'adult',
    { '75': 570, '80-85-90': 612, '95-100': 612 }, 100, ['sofiyaa 3200'], 'doz'],

  // ---------------- GROUP B : LITE VESTS ----------------
  ['lite-wf-rn', 'LITE WHITE FOLDING RN', 'Lite White Folding', 'RN', 'adult',
    { '75': 440, '80-85-90': 480, '95-100': 580, '105-110': 680 }, 90, ['lite white', 'lite white vest']],
  ['lite-wf-rns', 'LITE WHITE FOLDING RNS', 'Lite White Folding', 'RNS', 'adult',
    { '80-85-90': 630, '95-100': 730, '105-110': 830 }, 120, ['lite white rns']],
  ['lite-cf-rn', 'LITE COLOR FOLDING RN', 'Lite Color Folding', 'RN', 'adult',
    { '75': 490, '80-85-90': 530, '95-100': 630, '105-110': 730 }, 100, ['lite color', 'lite color vest']],
  ['lite-cf-rns', 'LITE COLOR FOLDING RNS', 'Lite Color Folding', 'RNS', 'adult',
    { '80-85-90': 680, '95-100': 780 }, 130, ['lite color rns']],
  ['lite-gym', 'LITE GYM VEST', 'Lite Gym Vest', '', 'adult',
    { '75': 660, '80-85-90': 660, '95-100': 660 }, 150, ['lite gym']],
  ['lite-gym-design', 'LITE GYM VEST DESIGN', 'Lite Gym Vest Design', '', 'adult',
    { '80-85-90': 740, '95-100': 740 }, 170, ['lite gym design']],

  // ---------------- GROUP B : LITE DRAWER, TRUNKS & BRIEF ----------------
  ['lite-icd', 'LITE INTERLOCK DRAWER I.C.D', 'Lite ICD', 'O/E', 'adult',
    { '75': 680, '80-85-90': 730, '95-100': 830, '105-110': 930 }, 140, ['lite icd', 'lite interlock drawer']],
  ['lite-ipd', 'LITE INNER POCKET DRAWER I.P.D', 'Lite IPD', 'O/E', 'adult',
    { '80-85-90': 910, '95-100': 1010 }, 170, ['lite ipd', 'ipd', 'lite inner pocket']],
  ['lite-opd', 'LITE OUTER POCKET DRAWER O.P.D', 'Lite OPD', 'O/E', 'adult',
    { '80-85-90': 880, '95-100': 980 }, 170, ['lite opd', 'opd', 'outer pocket drawer']],
  ['lite-iebrief', 'LITE I/E BRIEF', 'Lite Brief', 'I/E', 'adult',
    { '75': 410, '80-85-90': 440, '95-100': 490 }, 80, ['lite brief', 'lite ie brief']],
  ['lite-jokee', 'LITE FRONT OPEN JOKEE I/E', 'Lite FO Jokee', 'I/E', 'adult',
    { '75': 430, '80-85-90': 460, '95-100': 510 }, 90, ['lite jokee', 'lite front open']],
  ['lite-iedrawer', 'LITE DRAWER I/E', 'Lite Drawer', 'I/E', 'adult',
    { '80-85-90': 510, '95-100': 570 }, 100, ['lite drawer', 'lite ie drawer']],
  ['lite-ielt', 'LITE LONG TRUNK I/E', 'Lite Long Trunk', 'I/E', 'adult',
    { '80-85-90': 620, '95-100': 700, '105-110': 780 }, 120, ['lite long trunk', 'lite trunk', 'lite long trunk ie']],
  ['lite-oebrief', 'LITE O/E BRIEF', 'Lite Brief', 'O/E', 'adult',
    { '75': 430, '80-85-90': 460, '95-100': 510 }, 90, ['lite oe brief']],
  ['lite-oedrawer', 'LITE DRAWER O/E', 'Lite Drawer', 'O/E', 'adult',
    { '80-85-90': 530, '95-100': 590 }, 110, ['lite oe drawer']],
  ['lite-oelt', 'LITE LONG TRUNK O/E', 'Lite Long Trunk', 'O/E', 'adult',
    { '75': 600, '80-85-90': 640, '95-100': 720, '105-110': 800 }, 130, ['lite long trunk oe', 'lite trunk oe']],

  // ---------------- GROUP B : KARINA / BLOOMER / SAMEEZ ----------------
  ['karina-pl-ie', 'KARINA PLAIN I/E PENTEEZ', 'Karina Plain Penteez', 'I/E', 'kids',
    { '45-50-55': 230, '60-65-70': 250, '73': 280, '75': 320, '80-85-90': 350, '95-100': 390, '105-110': 430 }, 70,
    ['karina plain', 'karina']],
  ['karina-pr-ie', 'KARINA PRINT I/E PENTEEZ', 'Karina Print Penteez', 'I/E', 'kids',
    { '45-50-55': 240, '60-65-70': 260, '73': 290, '75': 340, '80-85-90': 370, '95-100': 410, '105-110': 450 }, 80,
    ['karina print']],
  ['karina-pl-oe', 'KARINA PLAIN O/E PENTEEZ', 'Karina Plain Penteez', 'O/E', 'kids',
    { '45-50-55': 240, '60-65-70': 260, '75': 330 }, null, ['karina plain oe']],
  ['karina-pr-oe', 'KARINA PRINT O/E PENTEEZ', 'Karina Print Penteez', 'O/E', 'kids',
    { '45-50-55': 250, '60-65-70': 270, '75': 350 }, null, ['karina print oe']],
  ['bloomer-plain', 'PLAIN I/E BLOOMER', 'Plain Bloomer', 'I/E', 'kids',
    { '45-50-55': 300, '60-65-70': 340, '75': 400, '80-85-90': 450 }, null, ['plain bloomer', 'bloomer']],
  ['bloomer-print', 'PRINT I/E BLOOMER', 'Print Bloomer', 'I/E', 'kids',
    { '45-50-55': 310, '60-65-70': 350, '75': 420 }, null, ['print bloomer']],
  ['lily-sameez', 'LILY PRINTED SAMEEZ', 'Lily Printed Sameez', '', 'kids',
    { '45-50-55': 330, '60-65-70': 360 }, null, ['lily sameez', 'lily']],
];

function expandPrices(groups) {
  const out = {};
  for (const [key, rate] of Object.entries(groups)) {
    for (const s of key.split('-')) out[Number(s)] = rate;
  }
  return out;
}

export const CATALOG = RAW.map(([id, name, short, shape, section, prices, mrp, aliases, unit]) => ({
  id, name, short, shape, section,
  priceGroups: prices,
  prices: expandPrices(prices),
  mrp,
  aliases: aliases || [],
  unit: unit || 'box',
}));

export const CATALOG_BY_ID = Object.fromEntries(CATALOG.map((p) => [p.id, p]));

export function rateFor(product, size) {
  if (!product) return null;
  const r = product.prices[size];
  return r == null ? null : r;
}
