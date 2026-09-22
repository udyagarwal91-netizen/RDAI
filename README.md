# Netlanding Rate Finder

A voice-driven price lookup for the Skipper Garments **Netlanding** price lists, built for
quoting rates to customers while showing samples. One self-contained `index.html` — open it
on a phone, add it to the home screen, and it behaves like an app.

**Live page:** https://claude.ai/artifact/EB4ARRhBEsAPxT6yANH898

## What it does

- **Ask by voice.** Tap the mic and say a product — *"Gold White RN size 90"*, *"Colinsight
  trunk 105"*, *"Loria camisole"*. The rate comes back on screen and is read aloud.
- **Confirms the price list.** On the first question of each session it asks which sheet to
  quote from — 20 Aug 2026 or 01 Jun 2026 — then remembers the answer. Saying *"June"* or
  *"August"* inside a question overrides it for that question.
- **Always shows the other list.** When a rate changed between the two sheets, the card shows
  the previous rate and the difference, so an old quote can be answered on the spot.
- **Full size ladder.** Name a size and you get one number; leave it out and you get every size
  as a tappable chip.
- **Browsable catalogue.** Every group from both sheets is below the answer, with the printed
  terms and conditions, for when voice is not practical.
- **Works offline** once loaded. Speech *recognition* needs a network connection (the browser
  streams audio to its speech service); typing and every rate lookup do not.

## Data

183 rates transcribed from the two supplied PDFs, held in `index.html`:

| List | Effective | Basis (as printed) |
|---|---|---|
| Current | 20 August 2026 | Per piece, GST paid, freight paid to destination, no CD / schemes / bonus |
| Previous | 01 June 2026 | "Per unit = 10pcs", GST inclusive, F.O.R. paid, CD allowed on 30–45 day payment |

The two sheets state their rate basis differently. The figures themselves are directly
comparable per piece, so the June sheet's *"per unit = 10pcs"* line looks like a leftover from
an earlier template — worth confirming with the office before relying on it.

### Two anomalies in the June PDF, flagged in the app

1. **Loria Plain / Hippy O/E, size 110** prints **74**. August prints **58**, and the row's own
   ladder (47, 52, 54, …) makes 74 look like a typo. Stored as printed, flagged on the card.
2. **Just Joey Bottoms** — the value cells sit one column to the right of the size headings, so
   the sheet appears to have a sixth, unlabelled size. Read here as 45-55 → 85, which lines up
   with the August sheet's layout. Flagged on every row in that group.

Both show a "Check this one" banner rather than being silently corrected.

## Updating for a new price list

1. Add a new object to `LISTS` in `index.html` with an `id`, `short`, `long`, `role`, `terms`
   and `groups`. Row shape is `[name, variant, pack, [prices…], mrp, packOf, note]`, with
   `N` (null) for a size that is not offered.
2. Add its size header set to `SZ` if it is not one of the existing ones.
3. Add a chip for it in the `.listpicker` markup and to the `chips` map.

Product names people say differently — brand mishearings, "round neck" for RN, "euro cut" for
Uro Cut — live in the `ALIAS` map. Add to it rather than renaming the price data.

## Repository layout

```
index.html    the whole app — data, matching engine, UI, voice
source-pdfs/  the two price lists the data was transcribed from
```
