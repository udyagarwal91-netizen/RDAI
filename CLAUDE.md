# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static, no-build web app (PWA) for a hosiery sales rep: it records the whole
shop visit as audio (MediaRecorder), sends the recording to Gemini which
transcribes it and returns only the order lines (small talk filtered out),
prices them from the Skipper Hosiery NE price list, and renders a JPEG in the
layout of the paper ORDER FORM. There is deliberately no live speech-to-text.

## Commands

- `npm start` – serve the folder at http://localhost:8080 (any static server works; mic needs localhost or HTTPS)
- `npm test` – run all tests (`node --test tests/*.test.mjs`, no dependencies; Gemini is tested with a fake fetch)
- Single test: `node --test --test-name-pattern "Shyam" tests/parser.test.mjs`
- No linter or bundler; plain ES modules loaded directly by the browser.

## Architecture

- `js/catalog.js` – the price list as data. Rates are keyed by size groups (`'80-85-90': 880`) and expanded to per-size `prices`. `ADULT_SIZES` / `KIDS_SIZES` are the order-form columns.
- `js/hindi.js` – Devanagari → romanised Hinglish (`toRomanScript` is what the transcript box shows; recognition stays hi-IN) (dictionary of product/order words, Hindi numbers 1–100, spoken English letters "आई सी डी", schwa-aware transliteration fallback). Runs first inside the parser's normalisation.
- `js/parser.js` – offline rule parser, used when there is no Gemini key ("Rebuild order from this text"): user corrections (Settings) + misheard-brand/code repair (`misheardBrand`: known mishearings like "UP"→Ruby and a sound-alike key, only right before a product word; `misheardCode`: ICT→ICD), then normalise speech text (number words incl. Hindi, spelled letters "i w d" → "iwd", synonyms, fuzzy match) → split into segments (product words followed by numbers) → score catalog products (IDF-weighted keywords + aliases, previous brand inherited as a "ditto") → read size/qty patterns (pairs, ranges, lists, "each"). Unknown codes like `JFS 2409` become custom lines. Pure module, tested in Node.
- `js/recorder.js` – MediaRecorder wrapper (opus ~24 kbps, screen wake lock) + IndexedDB copy of the last recording so nothing is lost offline.
- `js/gemini.js` – `analyzeConversation({audio|text})`: REST `generateContent` with the audio inline (Files API upload above ~14 MB), system instruction = order rules + full price list + user styles/corrections, `responseSchema` JSON (transcript, customer, lines with `heard` quote, not_order). `toOrderResult` maps it to the same shape as `parseTranscript`. Default model `gemini-3.8-flash` (changeable in Settings).
- `js/order.js` – order model + pricing (`lineTotals`, `orderTotals`).
- `js/form-render.js` – canvas drawing of the order form + price summary; export via `canvasToJpeg`.
- `js/app.js` – UI wiring, localStorage draft/saved orders/settings, share/download.

## Conventions

- Keep the app build-free: only browser-native ES modules; tests import the same files.
- Any change to parsing behaviour should come with a transcript test in `tests/parser.test.mjs` (the existing ones mirror the four real paper forms).
- Add cache-busting to `sw.js` (`CACHE` name) when adding/renaming app files.
