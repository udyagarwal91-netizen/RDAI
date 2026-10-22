"""Posting approved drafts to Instagram and Facebook (Meta Graph API v26.0).

How it works:
- Approving a draft puts one "post" per platform in the queue at the draft's time.
- Every minute the timer posts whatever is due (unless you pressed Pause).
- Instagram only takes pictures from a public web address, so each picture is
  first uploaded to your Facebook Page as a hidden (unpublished) photo, and
  Instagram is given that photo's address.
- A failed post is retried twice (after 5 and 10 minutes), then you get a
  Telegram alert with a "Try again" button. If the laptop was off and a post
  is more than 6 hours late, it is NOT posted automatically — you're asked.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from app.brands import Brand
from app.db import connect, get_state, set_state

log = logging.getLogger(__name__)
TIMEOUT = 60
MAX_ATTEMPTS = 3
LATE_LIMIT = timedelta(hours=6)
IG_CAPTION_LIMIT = 2200
IG_HASHTAG_LIMIT = 30

_page_tokens: dict[str, str] = {}


class PublishError(Exception):
    """A problem posting, with a message the owner can act on."""


# ---------------------------------------------------------------- Meta calls

def _base(brand: Brand) -> str:
    return f"https://graph.facebook.com/{brand.config['meta'].get('graph_api_version', 'v26.0')}"


def _explain(body: dict, status: int) -> str:
    err = body.get("error") or {}
    code, msg = err.get("code"), err.get("error_user_msg") or err.get("message") or f"error {status}"
    if code == 190:
        return "Meta says the access token is no longer valid. Make a new System User token (Connect accounts → Instagram + Facebook)."
    if code in (10, 200, 3):
        return f"Meta says the token is missing a permission ({msg}). Regenerate the token with all the ticks listed on Connect accounts."
    if code == 4 or code == 17 or code == 32:
        return "Meta's rate limit was reached. The post will be retried later."
    if code == 368:
        return f"Meta blocked this action for now ({msg})."
    return f"Meta said: {msg}"


def _call(method: str, url: str, token: str, files=None, **params) -> dict:
    params["access_token"] = token
    try:
        r = requests.request(method, url, data=params if method == "POST" else None,
                             params=params if method == "GET" else None, files=files, timeout=TIMEOUT)
    except requests.RequestException:
        raise PublishError("Couldn't reach Meta. Check your internet connection.") from None
    body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
    if r.status_code != 200 or "error" in body:
        raise PublishError(_explain(body, r.status_code))
    return body


def page_token(brand: Brand) -> str:
    """Pages need a Page access token; the System User token can fetch it."""
    if brand.slug in _page_tokens:
        return _page_tokens[brand.slug]
    user_token = brand.secret("META_ACCESS_TOKEN")
    page_id = str(brand.config["meta"].get("facebook_page_id") or "")
    if not user_token or not page_id:
        raise PublishError("Instagram + Facebook isn't connected yet (Connect accounts → Instagram + Facebook).")
    body = _call("GET", f"{_base(brand)}/{page_id}", user_token, fields="access_token")
    if not body.get("access_token"):
        raise PublishError("Couldn't get permission to post on your Facebook Page. Check the Page is assigned to the "
                           "system user with Full control.")
    _page_tokens[brand.slug] = body["access_token"]
    return body["access_token"]


def _upload_hidden_photos(brand: Brand, draft: dict) -> list[dict]:
    """Upload the draft's pictures to the Page as unpublished photos (once) → [{"id", "url"}]."""
    key = f"fbphotos:{draft['id']}:{draft['attempt']}"
    cached = get_state(brand.slug, key)
    if cached:
        return json.loads(cached)
    token = page_token(brand)
    page_id = brand.config["meta"]["facebook_page_id"]
    photos = []
    for path in draft["files"]:
        with open(path, "rb") as fh:
            body = _call("POST", f"{_base(brand)}/{page_id}/photos", token, files={"source": fh}, published="false")
        info = _call("GET", f"{_base(brand)}/{body['id']}", token, fields="images")
        best = max(info.get("images") or [], key=lambda i: i.get("width", 0), default=None)
        if not best:
            raise PublishError("Facebook accepted the photo but didn't return its address. Try again later.")
        photos.append({"id": body["id"], "url": best["source"]})
    set_state(brand.slug, key, json.dumps(photos))
    return photos


def _is_reel(draft: dict) -> bool:
    return bool(draft["files"]) and draft["files"][0].suffix == ".mp4"


def publish_facebook(brand: Brand, draft: dict) -> tuple[str, str]:
    token = page_token(brand)
    page_id = brand.config["meta"]["facebook_page_id"]
    if _is_reel(draft):
        # Videos go to Meta's video upload address; the post id is used later for results.
        ver = brand.config["meta"].get("graph_api_version", "v26.0")
        with open(draft["files"][0], "rb") as fh:
            body = _call("POST", f"https://graph-video.facebook.com/{ver}/{page_id}/videos", token,
                         files={"source": fh}, description=draft["caption"], published="true")
        video_id = body["id"]
        info = _call("GET", f"{_base(brand)}/{video_id}", token, fields="permalink_url,post_id")
        link = info.get("permalink_url", "")
        if link.startswith("/"):
            link = "https://www.facebook.com" + link
        return info.get("post_id") or video_id, link
    if len(draft["files"]) == 1:
        with open(draft["files"][0], "rb") as fh:
            body = _call("POST", f"{_base(brand)}/{page_id}/photos", token, files={"source": fh},
                         caption=draft["caption"], published="true")
        post_id = body.get("post_id") or body["id"]
    else:
        photos = _upload_hidden_photos(brand, draft)
        attached = {f"attached_media[{i}]": json.dumps({"media_fbid": p["id"]}) for i, p in enumerate(photos)}
        body = _call("POST", f"{_base(brand)}/{page_id}/feed", token, message=draft["caption"], **attached)
        post_id = body["id"]
    link = _call("GET", f"{_base(brand)}/{post_id}", token, fields="permalink_url").get("permalink_url", "")
    return post_id, link


def _wait_ready(brand: Brand, container_id: str, token: str, tries: int = 24) -> None:
    for _ in range(tries):  # 24 tries ≈ 2 minutes
        status = _call("GET", f"{_base(brand)}/{container_id}", token, fields="status_code").get("status_code")
        if status in ("FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError("Instagram couldn't process the picture. Check it's a normal photo and try again.")
        time.sleep(5)
    raise PublishError("Instagram took too long to process the picture. It will be retried.")


def publish_instagram(brand: Brand, draft: dict) -> tuple[str, str]:
    ig_id = str(brand.config["meta"].get("instagram_account_id") or "")
    if not ig_id:
        raise PublishError("Your Instagram account ID isn't set. Press Save & test on Connect accounts → Instagram + Facebook.")
    caption = draft["caption"]
    if len(caption) > IG_CAPTION_LIMIT:
        raise PublishError(f"The caption is {len(caption)} characters; Instagram allows {IG_CAPTION_LIMIT}. Edit it shorter.")
    if caption.count("#") > IG_HASHTAG_LIMIT:
        raise PublishError(f"The caption has more than {IG_HASHTAG_LIMIT} hashtags, which Instagram doesn't allow.")
    token = page_token(brand)
    base = f"{_base(brand)}/{ig_id}"
    if _is_reel(draft):
        container = _upload_reel(brand, draft, base, token, caption)
        _wait_ready(brand, container, token, tries=72)  # videos take longer: up to ~6 minutes
        media_id = _call("POST", f"{base}/media_publish", token, creation_id=container)["id"]
        link = _call("GET", f"{_base(brand)}/{media_id}", token, fields="permalink").get("permalink", "")
        return media_id, link
    photos = _upload_hidden_photos(brand, draft)
    if len(photos) == 1:
        container = _call("POST", f"{base}/media", token, image_url=photos[0]["url"], caption=caption)["id"]
    else:
        children = [_call("POST", f"{base}/media", token, image_url=p["url"], is_carousel_item="true")["id"]
                    for p in photos[:20]]
        for child in children:
            _wait_ready(brand, child, token)
        container = _call("POST", f"{base}/media", token, media_type="CAROUSEL", children=",".join(children),
                          caption=caption)["id"]
    _wait_ready(brand, container, token)
    media_id = _call("POST", f"{base}/media_publish", token, creation_id=container)["id"]
    link = _call("GET", f"{_base(brand)}/{media_id}", token, fields="permalink").get("permalink", "")
    return media_id, link


def _upload_reel(brand: Brand, draft: dict, base: str, token: str, caption: str) -> str:
    """Instagram reel: make a resumable container, then upload the video file straight from this computer."""
    container = _call("POST", f"{base}/media", token, media_type="REELS", upload_type="resumable",
                      caption=caption, share_to_feed="true")["id"]
    ver = brand.config["meta"].get("graph_api_version", "v26.0")
    data = draft["files"][0].read_bytes()
    try:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{ver}/{container}", data=data, timeout=600,
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))})
    except requests.RequestException:
        raise PublishError("The reel upload to Instagram was interrupted. It will be retried.") from None
    body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
    if r.status_code != 200 or not body.get("success", True) or "error" in body:
        raise PublishError(_explain(body, r.status_code) if "error" in body else
                           f"Instagram didn't accept the reel upload (error {r.status_code}).")
    return container


PLATFORMS = {"instagram": publish_instagram, "facebook": publish_facebook}
PLATFORM_NAMES = {"instagram": "Instagram", "facebook": "Facebook"}


# ---------------------------------------------------------------- the queue

def _now(brand: Brand) -> datetime:
    return datetime.now(ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata")))


def platforms(brand: Brand) -> list[str]:
    chosen = (brand.config.get("publishing") or {}).get("platforms") or ["instagram", "facebook"]
    return [p for p in chosen if p in PLATFORMS]


def next_free_slot(brand: Brand) -> datetime:
    """The next posting slot (from Brand → Posting days) that has nothing queued yet."""
    from app.planner import plan_slots

    now = _now(brand)
    with connect() as conn:
        taken = {r["scheduled_for"][:16] for r in conn.execute(
            "SELECT scheduled_for FROM posts WHERE brand = ? AND status IN ('queued', 'publishing', 'published')",
            (brand.slug,))}
    for week in range(4):
        for slot in plan_slots(brand, now.date() + timedelta(days=7 * week)):
            if slot.when > now + timedelta(minutes=10) and slot.when.isoformat()[:16] not in taken:
                return slot.when
    return now + timedelta(hours=1)


def queue_draft(brand: Brand, draft_id: int) -> datetime:
    """Put an approved draft in the queue for each platform. Returns when it will go out."""
    now = _now(brand)
    with connect() as conn:
        d = conn.execute("SELECT scheduled_for FROM drafts WHERE id = ? AND brand = ?", (draft_id, brand.slug)).fetchone()
    when = datetime.fromisoformat(d["scheduled_for"]) if d and d["scheduled_for"] else None
    if when is None:
        when = next_free_slot(brand)
    elif when < now:
        when = now  # approved after its time: post as soon as possible
    with connect() as conn:
        conn.execute("UPDATE drafts SET scheduled_for = ? WHERE id = ?", (when.isoformat(timespec="seconds"), draft_id))
        for platform in platforms(brand):
            exists = conn.execute("SELECT id FROM posts WHERE draft_id = ? AND platform = ? AND status != 'cancelled'",
                                  (draft_id, platform)).fetchone()
            if not exists:
                conn.execute("INSERT INTO posts (brand, draft_id, platform, scheduled_for, status) VALUES (?, ?, ?, ?, 'queued')",
                             (brand.slug, draft_id, platform, when.isoformat(timespec="seconds")))
    log.info("Draft #%d queued for %s on %s", draft_id, when.strftime("%a %d %b %H:%M"), ", ".join(platforms(brand)))
    return when


def is_paused(brand: Brand) -> bool:
    return get_state(brand.slug, "paused") == "1"


def set_paused(brand: Brand, paused: bool) -> None:
    set_state(brand.slug, "paused", "1" if paused else "0")
    log.info("Posting %s for %s", "PAUSED" if paused else "resumed", brand.slug)


def queue(brand: Brand, include_done: bool = False) -> list[dict]:
    """Posts grouped by draft, soonest first (for /queue and the Queue page)."""
    statuses = "('queued', 'publishing', 'failed', 'missed', 'published')" if include_done else \
               "('queued', 'publishing', 'failed', 'missed')"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM posts WHERE brand = ? AND status IN {statuses} ORDER BY scheduled_for DESC LIMIT 100"
            if include_done else
            f"SELECT * FROM posts WHERE brand = ? AND status IN {statuses} ORDER BY scheduled_for", (brand.slug,))]
    grouped: dict[int, dict] = {}
    for r in rows:
        g = grouped.setdefault(r["draft_id"], {"draft_id": r["draft_id"], "when": datetime.fromisoformat(r["scheduled_for"]),
                                                "posts": []})
        g["posts"].append(r)
    return list(grouped.values())


def publish_due(brand: Brand, notify) -> int:
    """Post everything that's due. `notify(text, buttons=None)` tells the owner. Returns posts published."""
    recover_stuck(brand, notify)
    if is_paused(brand):
        return 0
    from app import producer

    now = _now(brand)
    with connect() as conn:
        due = [dict(r) for r in conn.execute(
            "SELECT * FROM posts WHERE brand = ? AND status = 'queued' AND scheduled_for <= ? ORDER BY scheduled_for",
            (brand.slug, now.isoformat()))]
    published = 0
    for post in due:
        scheduled = datetime.fromisoformat(post["scheduled_for"])
        name = PLATFORM_NAMES[post["platform"]]
        if post["attempts"] == 0 and now - scheduled > LATE_LIMIT:
            _update(post["id"], status="missed")
            notify(f"⏰ Draft #{post['draft_id']} was due on {name} at {scheduled:%a %d %b, %I:%M %p}, but the agent "
                   "wasn't running then. Post it now?", [("📤 Post now", f"p:{post['id']}"), ("✖️ Skip", f"s:{post['id']}")])
            continue
        draft = producer.get_draft(brand, post["draft_id"])
        if not draft or draft["status"] not in ("approved", "published"):
            _update(post["id"], status="cancelled", last_error="the draft is no longer approved")
            continue
        _update(post["id"], status="publishing", attempts=post["attempts"] + 1,
                published_at=_now(brand).isoformat(timespec="seconds"))  # start time, overwritten on success
        try:
            try:
                platform_id, link = PLATFORMS[post["platform"]](brand, draft)
            except PublishError:
                raise
            except Exception as unexpected:  # odd answer from Meta, missing file… — treat like any failure
                log.exception("Unexpected problem posting draft #%d to %s", post["draft_id"], name)
                raise PublishError(f"Something unexpected happened ({type(unexpected).__name__}).") from None
        except PublishError as e:
            attempts = post["attempts"] + 1
            if attempts < MAX_ATTEMPTS:
                retry_at = now + timedelta(minutes=5 * attempts)
                _update(post["id"], status="queued", scheduled_for=retry_at.isoformat(timespec="seconds"), last_error=str(e))
                log.warning("%s post for draft #%d failed (try %d), retrying at %s: %s", name, post["draft_id"],
                            attempts, retry_at.strftime("%H:%M"), e)
            else:
                _update(post["id"], status="failed", last_error=str(e))
                log.error("%s post for draft #%d failed: %s", name, post["draft_id"], e)
                notify(f"❌ Couldn't post draft #{post['draft_id']} to {name}: {e}", [("🔁 Try again", f"p:{post['id']}")])
            continue
        _update(post["id"], status="published", platform_post_id=platform_id, permalink=link,
                published_at=_now(brand).isoformat(timespec="seconds"), last_error=None)
        published += 1
        log.info("Posted draft #%d to %s: %s", post["draft_id"], name, link)
        notify(f"✅ Posted draft #{post['draft_id']} to {name}." + (f"\n{link}" if link else ""))
        _maybe_mark_published(post["draft_id"])
    return published


def recover_stuck(brand: Brand, notify) -> None:
    """Posts left "posting now…" for over 15 minutes (e.g. the laptop shut down mid-upload) may or may not have gone
    out. Don't risk a duplicate: mark them failed and ask the owner to check."""
    cutoff = (_now(brand) - timedelta(minutes=15)).isoformat()
    with connect() as conn:
        stuck = [dict(r) for r in conn.execute(
            "SELECT * FROM posts WHERE brand = ? AND status = 'publishing' AND COALESCE(published_at, scheduled_for) < ?",
            (brand.slug, cutoff))]
    for post in stuck:
        name = PLATFORM_NAMES[post["platform"]]
        _update(post["id"], status="failed", last_error="interrupted while posting — check if it went out")
        notify(f"⚠️ Posting draft #{post['draft_id']} to {name} was interrupted. Please check {name}: if it's not "
               "there, press Post now.", [("📤 Post now", f"p:{post['id']}"), ("✅ It's there", f"s:{post['id']}")])


def retry_now(brand: Brand, post_id: int) -> None:
    _update(post_id, status="queued", scheduled_for=_now(brand).isoformat(timespec="seconds"), attempts=0)


def cancel(brand: Brand, post_id: int) -> None:
    _update(post_id, status="cancelled")


def _update(post_id: int, **fields) -> None:
    with connect() as conn:
        conn.execute(f"UPDATE posts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), post_id])


def _maybe_mark_published(draft_id: int) -> None:
    with connect() as conn:
        left = conn.execute("SELECT COUNT(*) FROM posts WHERE draft_id = ? AND status IN ('queued', 'publishing')",
                            (draft_id,)).fetchone()[0]
        if not left:
            conn.execute("UPDATE drafts SET status = 'published' WHERE id = ?", (draft_id,))
