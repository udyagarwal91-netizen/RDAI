# RDAI - Stock app for RDK

Calculates **closing stock as of any date** from:

```
Closing stock = Opening stock + Received from stitchers - Sales (ERP) +/- Adjustments
```

kept separately for three stock types:

| Stock type   | Comes from                              |
|--------------|-----------------------------------------|
| FRESH        | Received good pieces in full boxes of 10 |
| LOOSE        | Received good pieces not in a full box  |
| SECOND       | Addas (mistake) pieces received         |
| UNFINISHED   | Unfinished panels received (info stock) |

It also tracks every issue challan: what each stitcher still owes, and which book lines don't balance.

## Units, boxes and loose pieces

* 50.5 units of fresh received = **50 boxes + 5 loose pieces**. Fresh total = boxes + loose.
* Upload templates have **Boxes (Units)** and **Loose Pcs** columns (opening stock, sales, physical count).
* Packing loose pieces into boxes, or opening boxes: Adjustments page -> Repack.

* Issue, receive, ERP sales: **units** (1 unit = 10 pcs, one decimal: 7.2 = 72 pcs)
* Addas, short, unfinished: **pieces**
* Everything is stored and calculated in pieces.

## Start it

**Office computer (no Python needed):** download `RDAI-windows.zip` from the GitHub Releases page
(or the latest "Build Windows app" run -> Artifacts), extract it, double-click `RDAI.exe`.
A black window opens and the app opens in the browser. Close the black window to stop.

For developers: `./run_app.sh` (Linux/Mac) or `run_app.bat` (Windows with Python).

## Ask RDAI (assistant)

Chat page that answers questions from the stock engine ("stock of Colin Classic Trunk 90?",
"what does each stitcher owe?") and helps enter challans, receipts and sales. Anything that
changes data is shown as a proposal and saved only when you click **Confirm**.
Needs internet and a Claude API key (Settings page).

It opens in the browser at http://localhost:8501. To let other office computers use it, run
`streamlit run app.py --server.address 0.0.0.0` and open `http://<this-computer-ip>:8501`.

On first start the database is created from `data/seed/ledger_2026-04-01_to_2026-10-13.csv`
(challans 1-91 typed from the production book photos).

## Routine for staff

1. **Challans** - enter each new issue challan; record receipts as goods come back.
2. **Sales** - export the ERP sales report (date, item, size, qty) and upload. Unknown item names are asked once and remembered.
3. **Exceptions** - clear lines that don't balance.
4. **Physical Count & Tally** - upload a count any day; see shortages/excess; post adjustments once verified.

## Back-up

The live data is the single file `data/rdai.db`. Copy it to a pen drive / Google Drive daily.
