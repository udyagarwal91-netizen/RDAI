# Voice Order Book

Take orders by talking with your customer. The app listens to the conversation,
picks out the order (product → size → boxes), prices it from the
**Skipper Hosiery NE price list (15-05-2026)** and produces a JPEG laid out like
the paper **ORDER FORM**, ready to share on WhatsApp.

## How to use (on the phone)

1. Open the app in **Chrome** (Android) – needs HTTPS for the microphone.
2. Fill the party name, or just say it: *“party name Manoj Textiles from Sibsagar”*.
3. Tap **Start listening** and talk normally. Say each item as
   *product → size → quantity*:
   - `Ruby IWD 85 two, 90 three, 95 two, 100 two`
   - `Lite ICD 85 aur 90 mein 5 5 box`
   - `Ezee colour RN 85 se 100 tak 2 box each`
   - `ditto long trunk O E 85 5 90 10` (no brand = same brand as the line before)
   - Styles not in the price list: `JFS 2409 net 60 5 65 5 ...`, then just `2505 ...`
     (keeps the JFS prefix), `AHW 613 WSP minus 15 percent 85 to 100 2 each`
   - Corrections: `Ruby ICD 85 5` (overrides), `cancel Lite brief`
4. Small talk (“rate kya hai”, greetings…) is ignored and listed under
   *Treated as conversation* so nothing silently disappears.
5. Check / edit the lines, then **Share** or **Download JPEG**.

Optional: add an Anthropic API key in **Settings** and use **✨ Build with AI**
for messy, long conversations (Claude reads the transcript against the price
list). The key stays on the phone.

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
