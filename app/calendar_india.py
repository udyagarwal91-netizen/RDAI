"""Indian festivals and sale days the planner can plan around.

Only dates we're confident about are listed. Dates that follow the lunar
calendar can shift by a day by region, so they're marked "approx" and the
planner is told not to promise anything on the exact day. Add your own sale
days on the Brand page (Sale days & festivals); those are always used.
"""

from datetime import date

# (date, name, note)
KNOWN_DATES: list[tuple[date, str, str]] = [
    (date(2026, 11, 6), "Dhanteras", "approx (2 days before Diwali); festive shopping"),
    (date(2026, 11, 8), "Diwali", "biggest festive shopping moment; festive creatives"),
    (date(2026, 11, 27), "Black Friday", "online sale day — only mention real Shopify prices"),
    (date(2026, 11, 30), "Cyber Monday", "online sale day — only mention real Shopify prices"),
    (date(2026, 12, 25), "Christmas", "winter / gifting mood"),
    (date(2026, 12, 31), "New Year's Eve", "fresh-start / restock essentials angle"),
    (date(2027, 1, 1), "New Year", "fresh-start / restock essentials angle"),
    (date(2027, 1, 14), "Makar Sankranti / Lohri / Pongal", "approx; regional harvest festivals"),
    (date(2027, 1, 26), "Republic Day", "patriotic; Made-in-India angle; Republic Day sales common"),
    (date(2027, 3, 22), "Holi", "approx; colours — keep it product-safe"),
    (date(2027, 8, 15), "Independence Day", "Made-in-India angle; sales common"),
]


def upcoming(start: date, end: date, custom: list[dict] | None = None) -> list[dict]:
    """Festivals and sale days between start and end, plus a week of look-ahead
    so the planner can build up to them."""
    from datetime import timedelta

    horizon = end + timedelta(days=7)
    items = [{"date": d.isoformat(), "name": n, "note": note}
             for d, n, note in KNOWN_DATES if start <= d <= horizon]
    for c in custom or []:
        try:
            d = date.fromisoformat(str(c.get("date")))
        except ValueError:
            continue
        if start <= d <= horizon:
            items.append({"date": d.isoformat(), "name": str(c.get("name", "")),
                          "note": str(c.get("note", "")) + " (your own sale day)"})
    return sorted(items, key=lambda x: x["date"])
