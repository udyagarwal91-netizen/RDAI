# Voice Order Book

Take orders by talking with your customer. The app listens to the conversation,
picks out the order (product → size → boxes), prices it from the
**Skipper Hosiery NE price list (15-05-2026)** and produces a JPEG laid out like
the paper **ORDER FORM**, ready to share on WhatsApp.

## How to use (on the phone)

1. Open the app, tap **⚙ Settings** and paste your **Gemini API key**
   (from https://aistudio.google.com/apikey). It stays on the phone.
2. Walking into a shop: tap **Start recording**. Talk normally – Hindi,
   Hinglish or English, small talk included. Keep the app open.
3. Leaving: tap **Stop & analyze**. Gemini listens to the whole
   conversation, writes it out in English letters, leaves out everything that
   is not the order, and fills in the party name, place and order lines.
   Each line shows what was *heard*, so you can check it.
4. Correct anything, then **Share (WhatsApp…)** or **Download JPEG**.

The last recording is kept on the phone; if there is no signal, tap
**Analyze recording** later. You can also **pick a recording** made with the
phone's voice recorder or a WhatsApp voice note.

## Run locally

```bash
npm start          # serves on http://localhost:8080
npm test           # parser + pricing tests (Node 18+, no dependencies)
```

## Deploy

Push to `main`; the GitHub Actions workflow runs the tests and publishes the
site with GitHub Pages (enable *Settings → Pages → Source: GitHub Actions* once).

## Updating prices

All products and rates live in `js/catalog.js`. When a new price list comes
out, edit the rates there (sizes are grouped exactly as on the list, e.g.
`'80-85-90': 880`) and run `npm test`.
