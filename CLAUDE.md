# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static, no-build web app (PWA) for a hosiery sales rep: it listens to an
order conversation (Web Speech API), extracts order lines, prices them from the
Skipper Hosiery NE price list, and renders a JPEG in the layout of the paper
ORDER FORM.

## Commands

- `npm start` – serve the folder at http://localhost:8080 (any static server works; mic needs localhost or HTTPS)
- `npm test` – run all tests (`node --test tests/*.test.mjs`, no dependencies)
- Single test: `node --test --test-name-pattern "Shyam" tests/parser.test.mjs`
- No linter or bundler; plain ES modules loaded directly by the browser.

## Architecture

- `js/catalog.js` – the price list as data. Rates are keyed by size groups (`'80-85-90': 880`) and expanded to per-size `prices`. `ADULT_SIZES` / `KIDS_SIZES` are the order-form columns.
- `js/parser.js` – offline rule parser: normalise speech text (number words incl. Hindi, spelled letters "i w d" → "iwd", synonyms, fuzzy match) → split into segments (product words followed by numbers) → score catalog products (IDF-weighted keywords + aliases, previous brand inherited as a "ditto") → read size/qty patterns (pairs, ranges, lists, "each"). Unknown codes like `JFS 2409` become custom lines. Pure module, tested in Node.
- `js/ai.js` – optional Claude parser (Anthropic SDK from jsDelivr, browser-side, structured JSON output). Must return the same shape as `parseTranscript`.
- `js/order.js` – order model + pricing (`lineTotals`, `orderTotals`).
- `js/form-render.js` – canvas drawing of the order form + price summary; export via `canvasToJpeg`.
- `js/speech.js` – Web Speech wrapper that auto-restarts during pauses.
- `js/app.js` – UI wiring, localStorage draft/saved orders/settings, share/download.

## Conventions

- Keep the app build-free: only browser-native ES modules; tests import the same files.
- Any change to parsing behaviour should come with a transcript test in `tests/parser.test.mjs` (the existing ones mirror the four real paper forms).
- Add cache-busting to `sw.js` (`CACHE` name) when adding/renaming app files.
