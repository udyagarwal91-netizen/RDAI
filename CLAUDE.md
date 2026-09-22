# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

A single-page voice price-lookup tool for the Skipper Garments Netlanding price lists, used in
the field to quote rates to customers. See `README.md` for behaviour and for how to add a new
price list.

## Commands

There is no build, test, or lint setup — the app is one static file with no dependencies.

- **Run it:** open `index.html` in a browser, or serve the directory
  (`python3 -m http.server 8000`) and open it on a phone on the same network.
- **Check the JavaScript before committing:** the file has no build step, so a syntax error
  ships silently. Extract and check it:
  ```sh
  python3 -c "import re;s=open('index.html').read();open('/tmp/app.js','w').write(re.search(r'<script>(.*)</script>',s,re.S).group(1))"
  node --check /tmp/app.js
  ```
- **Exercise the matcher:** the data and engine (everything above the `STATE` banner) run in
  plain Node with no DOM. Slice that portion out and call `search(query, "aug")` directly —
  this is the fastest way to confirm a change to `ALIAS`, the scoring, or size handling has
  not regressed real queries.

## Architecture

`index.html` is the entire application, in four sections in this order:

1. **Markup and CSS.** The file is published as a claude.ai Artifact, so it carries no
   `<!doctype>`, `<html>`, `<head>` or `<body>` — those are supplied at publish time. Colours
   are tokens on `:root`, redefined under both `prefers-color-scheme: dark` and
   `:root[data-theme="dark"]`. Never define a colour only inside one of those blocks.
2. **`LISTS` data.** Both price lists, transcribed from `source-pdfs/`. Rows are compact arrays;
   `ITEMS` flattens them at load into one searchable array of 183 rates.
3. **Matching engine.** `norm` → `tokenise` → `canon` (via `ALIAS`) → `search`. Scoring is
   exact 3 / prefix 2.0 / Levenshtein-1 1.4, plus 5 for an adjacent-word bigram that matches a
   style name ("hi cut" → Hi-Cut), plus ±0.9 for whether the row actually carries a rate at the
   requested size. A result answers directly when it leads on score by 1.6 or matches strictly
   more query terms than the runner-up; otherwise the picker is shown.
4. **UI, voice, state.** `renderAnswer` / `renderPicker` / `renderBrowse`, Web Speech in and
   out, and `localStorage`-backed settings.

## Conventions

- **Price data is transcribed, never inferred.** Where a source PDF is wrong or ambiguous,
  store what it prints and put the explanation in the row's `note` field so the app shows a
  "Check this one" banner. Do not silently correct a sheet.
- **Speech variants belong in `ALIAS`,** not in the price data. The right-hand side of an alias
  must be a token that actually appears in a product name.
- **Anything the user hears must match what is on screen** — `speakAnswer` and `renderAnswer`
  read the same row and size index.
- Wrap every `localStorage` access in try/catch; it throws in private windows.
