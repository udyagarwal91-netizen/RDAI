"""The database: one SQLite file at data/agent.db.

Every table has a `brand` column so client brands can share one app
without their data mixing. Times are stored as ISO text in IST
(e.g. 2026-10-20T19:30:00+05:30) so they read naturally.
"""

import sqlite3
from contextlib import contextmanager

from app import DATA_DIR

DB_PATH = DATA_DIR / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    brand           TEXT NOT NULL,
    shopify_id      TEXT NOT NULL,            -- gid://shopify/Product/...
    handle          TEXT,
    title           TEXT NOT NULL,
    description     TEXT,
    product_type    TEXT,
    tags            TEXT,                     -- JSON list
    collections     TEXT,                     -- JSON list
    variants        TEXT,                     -- JSON list: size, colour, price, compare_at, sku, stock
    images          TEXT,                     -- JSON list of image URLs (first = main)
    min_price       REAL,
    total_stock     INTEGER,
    status          TEXT,                     -- ACTIVE / DRAFT / ARCHIVED
    shopify_created TEXT,
    synced_at       TEXT NOT NULL,
    PRIMARY KEY (brand, shopify_id)
);

CREATE TABLE IF NOT EXISTS content_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    week_start      TEXT NOT NULL,            -- date the plan covers from
    scheduled_for   TEXT NOT NULL,            -- planned posting time
    theme           TEXT NOT NULL,            -- product / offer / education / lifestyle
    format          TEXT NOT NULL,            -- template_image / gemini_image / carousel / gemini_reel
    product_ids     TEXT,                     -- JSON list of shopify_id
    idea            TEXT NOT NULL,            -- one-line brief from the planner
    platforms       TEXT NOT NULL DEFAULT '["instagram","facebook"]',
    status          TEXT NOT NULL DEFAULT 'planned',  -- planned / drafted / skipped
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    plan_id         INTEGER REFERENCES content_plan(id),
    format          TEXT NOT NULL,
    caption         TEXT,
    hashtags        TEXT,
    media_paths     TEXT,                     -- JSON list of local files (images / video)
    scheduled_for   TEXT,
    quality_flags   TEXT,                     -- JSON list of problems found by the checker
    attempt         INTEGER NOT NULL DEFAULT 1,
    cost_usd        REAL NOT NULL DEFAULT 0,  -- AI spend to make this draft
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected / regenerating / failed
    telegram_msg_id TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    draft_id        INTEGER NOT NULL REFERENCES drafts(id),
    decision        TEXT NOT NULL,            -- approve / regenerate / edit_caption / reject / auto
    note            TEXT,                     -- e.g. the edited caption
    decided_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    draft_id        INTEGER NOT NULL REFERENCES drafts(id),
    platform        TEXT NOT NULL,            -- instagram / facebook
    scheduled_for   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued / publishing / published / failed / paused
    platform_post_id TEXT,
    permalink       TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    published_at    TEXT
);

CREATE TABLE IF NOT EXISTS insights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    post_id         INTEGER NOT NULL REFERENCES posts(id),
    collected_at    TEXT NOT NULL,
    reach           INTEGER,
    impressions     INTEGER,
    likes           INTEGER,
    comments        INTEGER,
    saves           INTEGER,
    shares          INTEGER,
    profile_visits  INTEGER,
    link_clicks     INTEGER,
    raw             TEXT                      -- full JSON from Meta, in case we need more later
);

-- Small key/value store for app state (paused flag, active brand, cached tokens' expiry, etc.)
CREATE TABLE IF NOT EXISTS app_state (
    brand           TEXT NOT NULL,
    key             TEXT NOT NULL,
    value           TEXT,
    PRIMARY KEY (brand, key)
);

-- The agent team's written briefs (market research, weekly direction, finance, SMO, campaign plans…)
CREATE TABLE IF NOT EXISTS team_briefs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    agent           TEXT NOT NULL,            -- researcher / planner / creator / smo / finance / manager
    kind            TEXT NOT NULL,            -- market / direction / plan_review / finance / smo / campaign_plan
    title           TEXT NOT NULL,
    body            TEXT,                     -- plain-English text shown to the owner
    data            TEXT,                     -- JSON with the structured details
    cost_usd        REAL DEFAULT 0,
    created_at      TEXT NOT NULL
);

-- Decisions the Manager makes, re-checked later so the team learns whether they worked
CREATE TABLE IF NOT EXISTS team_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    brief_id        INTEGER,
    decision        TEXT NOT NULL,
    reason          TEXT,
    expected        TEXT,                     -- what should happen if it's right
    check_on        TEXT,                     -- date to review it (YYYY-MM-DD)
    status          TEXT DEFAULT 'open',      -- open / worked / did_not_work / unclear
    outcome         TEXT,
    created_at      TEXT NOT NULL
);

-- Every task each agent works on: when, how long, what it cost, how it went (the agents' work log)
CREATE TABLE IF NOT EXISTS agent_work (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    agent           TEXT NOT NULL,
    task            TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    seconds         REAL,
    status          TEXT DEFAULT 'working',   -- working / done / failed
    cost_usd        REAL DEFAULT 0,
    result          TEXT
);

-- Changes to Meta ads proposed by the team. Nothing runs until the owner approves it.
CREATE TABLE IF NOT EXISTS ad_proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand           TEXT NOT NULL,
    agent           TEXT NOT NULL,
    action          TEXT NOT NULL,            -- pause / activate / budget / retarget / boost
    target_id       TEXT,                     -- Meta ad set / ad / campaign id, or Instagram post id
    target_name     TEXT,
    params          TEXT,                     -- JSON
    reason          TEXT,
    expected        TEXT,
    status          TEXT DEFAULT 'proposed',  -- proposed / approved / done / failed / rejected / expired
    result          TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);

-- Meta ads results per ad per day (Skipper Packs ad account only)
CREATE TABLE IF NOT EXISTS ads_daily (
    brand           TEXT NOT NULL,
    day             TEXT NOT NULL,            -- YYYY-MM-DD
    ad_id           TEXT NOT NULL,
    ad_name         TEXT,
    adset_id        TEXT,
    adset_name      TEXT,
    campaign_id     TEXT,
    campaign_name   TEXT,
    spend           REAL,
    impressions     INTEGER,
    reach           INTEGER,
    frequency       REAL,
    link_clicks     INTEGER,
    add_to_cart     INTEGER,
    checkouts       INTEGER,
    purchases       INTEGER,
    purchase_value  REAL,
    PRIMARY KEY (brand, day, ad_id)
);

-- Shopify sales per day (for blended ROAS, order value, refunds, COD share, repeat buyers)
CREATE TABLE IF NOT EXISTS sales_daily (
    brand           TEXT NOT NULL,
    day             TEXT NOT NULL,
    orders          INTEGER,
    revenue         REAL,
    refunds         REAL,
    cod_orders      INTEGER,
    repeat_orders   INTEGER,
    cancelled       INTEGER,
    PRIMARY KEY (brand, day)
);

CREATE INDEX IF NOT EXISTS idx_posts_due ON posts(status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_briefs ON team_briefs(brand, kind, id);
CREATE INDEX IF NOT EXISTS idx_work ON agent_work(brand, agent, id);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(brand, status);
"""


# Columns added after the first release. init_db() adds any that are missing,
# so an existing database upgrades itself without losing data.
ADDED_COLUMNS = [
    ("products", "pack_size", "INTEGER"),        # 2 or 3, read from the title ("3 Pcs Pack")
    ("products", "max_price", "REAL"),
    ("products", "compare_at_min", "REAL"),      # lowest crossed-out (MRP) price, if any
    ("products", "url", "TEXT"),                 # product page on the website
    ("products", "issues", "TEXT"),              # JSON list of data mistakes found in Shopify (e.g. ₹0 price)
    ("content_plan", "hook", "TEXT"),            # suggested opening line
    ("content_plan", "notes", "TEXT"),           # why this post, or warnings for the owner
    ("drafts", "creative", "TEXT"),              # JSON: headline, slides, image style, scene, chosen photo
    ("drafts", "image_style", "TEXT"),           # product_only / dressed_model / website_model / template
    ("drafts", "theme", "TEXT"),
    ("drafts", "product_ids", "TEXT"),           # JSON list of shopify_id
]


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, kind in ADDED_COLUMNS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def get_state(brand: str, key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE brand = ? AND key = ?", (brand, key)).fetchone()
    return row["value"] if row else default


def set_state(brand: str, key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO app_state(brand, key, value) VALUES (?, ?, ?) "
                     "ON CONFLICT(brand, key) DO UPDATE SET value = excluded.value", (brand, key, value))


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
