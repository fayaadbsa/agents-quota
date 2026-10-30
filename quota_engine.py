#!/usr/bin/env python3
import json
import os
import sys
import time
import datetime
import re
import math
import cairo
import gi
import sqlite3
import glob
import hashlib
import urllib.request
import urllib.error

ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
CONFIG_DIR = os.path.expanduser("~/.config/claude-limits")
ACCOUNTS_PATH = os.path.join(CONFIG_DIR, "accounts.json")
NINEROUTER_DIR = os.path.expanduser("~/.9router")
NINEROUTER_DB = os.path.join(NINEROUTER_DIR, "db", "data.sqlite")

DEFAULT_FETCH_GAP = 300  # after a model hit, refetch that source at most this often
MIN_FORCE_GAP = 30       # manual "Refresh" can't fetch more often than this
MAX_STALE = 900          # refetch this often even with no hits (exhausted quota = no hits to trigger one)
_SOURCES = {}            # source -> {"accounts": [...], "ts": last fetch time}
CLAUDE_PROJECT_ROOTS = ["~/.claude/projects", "~/.claude-profiles/*/projects"]

# Last good result per remote source (fallback when a fetch fails)
_DEV_CACHE = {
    "claude_dev": {"data": None, "ts": 0},
    "claude_stag": {"data": None, "ts": 0},
    "codex": {"data": None, "ts": 0}
}


def _auto_discover_profile_settings():
    """Auto-discover dev and stag API keys/URLs from ~/.claude-profiles if not explicitly set."""
    p1 = os.path.expanduser("~/.claude-profiles/profile1/settings.override.json")
    p2 = os.path.expanduser("~/.claude-profiles/profile2/settings.override.json")
    discovered = {}
    if os.path.exists(p1):
        try:
            with open(p1) as f:
                env = json.load(f).get("env", {})
                discovered["dev_url"] = env.get("ANTHROPIC_BASE_URL", "https://9router-dev.qtn.ai")
                discovered["dev_key"] = env.get("ANTHROPIC_AUTH_TOKEN", "")
                discovered["dev_model"] = env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "dev-ff-dev")
        except Exception:
            pass
    if os.path.exists(p2):
        try:
            with open(p2) as f:
                env = json.load(f).get("env", {})
                discovered["stag_url"] = env.get("ANTHROPIC_BASE_URL", "https://9router-stag.quantumbyte.ai")
                discovered["stag_key"] = env.get("ANTHROPIC_AUTH_TOKEN", "")
                discovered["stag_model"] = env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "dev-ff-stag")
        except Exception:
            pass
    return discovered


def load_accounts_config():
    if not os.path.exists(ACCOUNTS_PATH):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        discovered = _auto_discover_profile_settings()
        default_config = {
            "settings": {
                "fetch_gap_seconds": DEFAULT_FETCH_GAP,
                "9router_dev_url": discovered.get("dev_url", "https://9router-dev.qtn.ai"),
                "9router_dev_api_key": discovered.get("dev_key", ""),
                "9router_dev_model": discovered.get("dev_model", "dev-ff-dev"),
                "9router_stag_url": discovered.get("stag_url", "https://9router-stag.quantumbyte.ai"),
                "9router_stag_api_key": discovered.get("stag_key", ""),
                "9router_stag_model": discovered.get("stag_model", "dev-ff-stag")
            },
            "accounts": []
        }
        save_accounts_config(default_config)
        return default_config

    try:
        with open(ACCOUNTS_PATH, "r") as f:
            cfg = json.load(f)
        settings = cfg.setdefault("settings", {})
        discovered = _auto_discover_profile_settings()
        changed = False
        if not settings.get("9router_dev_api_key") and discovered.get("dev_key"):
            settings["9router_dev_api_key"] = discovered["dev_key"]
            changed = True
        if not settings.get("9router_dev_url") and discovered.get("dev_url"):
            settings["9router_dev_url"] = discovered["dev_url"]
            changed = True
        if not settings.get("9router_dev_model"):
            settings["9router_dev_model"] = discovered.get("dev_model", "dev-ff-dev")
            changed = True
        if not settings.get("9router_stag_api_key") and discovered.get("stag_key"):
            settings["9router_stag_api_key"] = discovered["stag_key"]
            changed = True
        if not settings.get("9router_stag_url") and discovered.get("stag_url"):
            settings["9router_stag_url"] = discovered["stag_url"]
            changed = True
        if not settings.get("9router_stag_model"):
            settings["9router_stag_model"] = discovered.get("stag_model", "dev-ff-stag")
            changed = True
        if changed:
            save_accounts_config(cfg)
        return cfg
    except Exception as e:
        return {"settings": {}, "accounts": [], "error": str(e)}


def save_accounts_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # 0600: file holds the 9router API key
        fd = os.open(ACCOUNTS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


def get_9router_cli_token():
    """Compute local 9router CLI auth token from machine-id and cli-secret."""
    m_path = os.path.join(NINEROUTER_DIR, "machine-id")
    s_path = os.path.join(NINEROUTER_DIR, "auth", "cli-secret")
    if not (os.path.exists(m_path) and os.path.exists(s_path)):
        return None
    try:
        mid = open(m_path).read().strip()
        sec = open(s_path).read().strip()
        return hashlib.sha256((mid + "9r-cli-auth" + sec).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def format_countdown(iso_or_ts):
    if not iso_or_ts:
        return "N/A"
    try:
        if isinstance(iso_or_ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(iso_or_ts, datetime.timezone.utc)
        else:
            clean_str = str(iso_or_ts).replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_str).astimezone()
        now = datetime.datetime.now(dt.tzinfo)
        diff = dt - now
        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "ready / reset"

        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or not parts:
            parts.append(f"{minutes}m")

        return f"in {' '.join(parts)}"
    except Exception:
        return str(iso_or_ts)


def get_quota_badge(used, total):
    if used is None:
        return "⚪", "unknown", None
    if total <= 0:
        return "🟢", "ok", 100
    pct_used = (used / total) * 100.0
    pct_remaining = max(0.0, 100.0 - pct_used)

    if pct_used < 50:
        return "🟢", "ok", pct_remaining
    elif pct_used < 80:
        return "🟡", "warn", pct_remaining
    else:
        return "🔴", "danger", pct_remaining


def format_reset_hours(raw_reset_at):
    """Format remaining time until reset into a compact string like '4h' or '45m'."""
    if not raw_reset_at:
        return ""
    try:
        clean_str = str(raw_reset_at).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_str).astimezone()
        now = datetime.datetime.now(dt.tzinfo)
        diff_sec = int((dt - now).total_seconds())
        if diff_sec <= 0:
            return "0m"
        hours = diff_sec // 3600
        mins = (diff_sec % 3600) // 60
        if hours > 24:
            days = hours // 24
            return f"{days}d"
        elif hours > 0:
            if mins > 0 and hours < 3:
                return f"{hours}h{mins}m"
            return f"{hours}h"
        else:
            return f"{mins}m"
    except Exception:
        return ""


PROVIDER_LOGOS = {
    "antigravity": os.path.join(ICONS_DIR, "antigravity.png"),
    "claude": os.path.join(ICONS_DIR, "claude.png"),
    "codex": os.path.join(ICONS_DIR, "codex.png"),
}
TRAY_FONT = "Ubuntu Sans Bold 8.5"


def _level_rgb(pct_left):
    if pct_left >= 50:
        return (0.13, 0.77, 0.37)  # green #22c55e
    if pct_left >= 20:
        return (0.96, 0.62, 0.04)  # amber #f59e0b
    return (0.94, 0.27, 0.27)      # red #ef4444


def _pct_text(p):
    return "--" if p is None else f"{p:.0f}%"


def _pick_quota(quotas, keys):
    for q in quotas:
        if any(k in q["name"].lower() for k in keys):
            return q
    return None


def _quota_row(q):
    """(remaining %, time left). If the window already reset since last fetch, it's full again."""
    raw = q.get("raw_reset_at") or q.get("reset_at")  # processed rows carry the former, raw ones the latter
    used = q.get("used")
    if used is None:  # no reading available -- draw an empty ring, not a full one
        return None, format_reset_hours(raw)
    try:
        if raw and _iso_ts(raw) <= time.time():
            return 100.0, ""
    except ValueError:
        pass
    return max(0.0, 100.0 - float(used)), format_reset_hours(raw)


def tray_segments(data):
    """One segment per tray-visible account: provider + (remaining %, time left) for daily and weekly."""
    segs = []
    for acc in data.get("accounts", []):
        quotas = acc.get("quotas", [])
        if not acc.get("show_in_tray") or not quotas:
            continue
        daily = _pick_quota(quotas, ("5h", "session", "daily")) or quotas[0]
        weekly = _pick_quota(quotas, ("week", "7d"))
        rows = [_quota_row(q) for q in (daily, weekly) if q]
        segs.append({
            "provider": acc["provider"],
            "slug": acc.get("slug", "?"),
            "tag": acc.get("tag"),
            "name": acc.get("name", ""),
            "rows": rows
        })
    return segs


def draw_tray(segments, filename, h=32):
    """Render the whole tray strip: [logo] [outer ring=daily, inner ring=weekly] [pct time / pct time] per account."""
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    ring_d, logo_d, gap, seg_gap, col_gap = h * 0.75, h * 0.56, 5, 12, 4
    ring_w = 3.0

    def layout(ctx, text):
        lo = PangoCairo.create_layout(ctx)
        lo.set_font_description(Pango.FontDescription.from_string(TRAY_FONT))
        lo.set_markup(f'<span font_features="tnum">{text}</span>', -1)
        return lo, lo.get_pixel_size()

    # Measure pass: widest pct / time text per segment so both rows align in columns
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    plans = []
    for s in segments:
        pct_w = max(layout(probe, _pct_text(p))[1][0] for p, _ in s["rows"])
        time_w = max((layout(probe, t)[1][0] for _, t in s["rows"] if t), default=0)
        text_w = pct_w + (col_gap + time_w if time_w else 0)
        plans.append((pct_w, text_w))
    total_w = sum(logo_d + gap + ring_d + gap + tw for _, tw in plans) + seg_gap * max(0, len(plans) - 1)
    # >= 1.5*h keeps appindicator from squashing it into a square icon slot
    w = max(int(math.ceil(total_w)), int(h * 1.5) + 1)

    # SVG, not PNG: appindicator rasterizes it at the panel's real scale (125% here) -> no upscale blur
    os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
    surface = cairo.SVGSurface(filename, w, h)
    ctx = cairo.Context(surface)
    cy = h / 2.0
    x = 0.0
    for s, (pct_w, text_w) in zip(segments, plans):
        # Logo (fallback: slug text)
        logo = PROVIDER_LOGOS.get(s["provider"])
        if logo and os.path.exists(logo):
            img = cairo.ImageSurface.create_from_png(logo)
            k = logo_d / img.get_height()
            ctx.save()
            ctx.translate(x, cy - logo_d / 2)
            ctx.scale(k, k)
            ctx.set_source_surface(img, 0, 0)
            ctx.get_source().set_filter(cairo.FILTER_BEST)
            ctx.paint()
            ctx.restore()

            tag = s.get("tag")
            if not tag and s.get("provider") == "claude":
                sname = (s.get("name") or s.get("slug") or "").lower()
                if "dev" in sname:
                    tag = "D"
                elif "stag" in sname:
                    tag = "S"

            if tag:
                badge_r = 4.5
                bx = x + logo_d - badge_r + 1
                by = cy + logo_d / 2 - badge_r + 1
                ctx.new_path()
                ctx.set_source_rgba(0.07, 0.07, 0.11, 1.0)
                ctx.arc(bx, by, badge_r + 1.2, 0, 2 * math.pi)
                ctx.fill()
                ctx.new_path()
                if str(tag).lower().startswith("d"):
                    ctx.set_source_rgb(0.96, 0.62, 0.04)  # amber for dev
                else:
                    ctx.set_source_rgb(0.65, 0.52, 0.98)  # purple for stag
                ctx.arc(bx, by, badge_r, 0, 2 * math.pi)
                ctx.fill()

                tlo = PangoCairo.create_layout(ctx)
                tlo.set_font_description(Pango.FontDescription.from_string("Ubuntu Sans Bold 6"))
                tlo.set_markup(f'<span font_features="tnum">{str(tag)[0].upper()}</span>', -1)
                tw, th = tlo.get_pixel_size()
                ctx.set_source_rgb(1, 1, 1)
                ctx.move_to(bx - tw / 2.0, by - th / 2.0)
                PangoCairo.show_layout(ctx, tlo)
        else:
            lo, (lw, lh) = layout(ctx, s["slug"])
            ctx.set_source_rgba(1, 1, 1, 0.9)
            ctx.move_to(x + (logo_d - lw) / 2, cy - lh / 2)
            PangoCairo.show_layout(ctx, lo)
        x += logo_d + gap

        # Rings: outer = daily, inner = weekly
        cx = x + ring_d / 2
        radii = [ring_d / 2 - ring_w / 2, ring_d / 2 - ring_w * 2.2]
        ctx.set_line_width(ring_w)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        for (pct, _), r in zip(s["rows"], radii):
            ctx.new_path()
            ctx.set_source_rgba(1, 1, 1, 0.14)
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
            ctx.stroke()
            if pct is not None and pct > 0:
                ctx.new_path()
                ctx.set_source_rgb(*_level_rgb(pct))
                ctx.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * min(pct, 100) / 100)
                ctx.stroke()
        x += ring_d + gap

        # Text rows: right-aligned pct column, dimmer time column
        lines = [layout(ctx, _pct_text(p)) + (layout(ctx, t) if t else (None, (0, 0))) for p, t in s["rows"]]
        line_h = max(sz[1] for _, sz, *_ in lines)
        top = cy - line_h * len(lines) / 2
        for i, (plo, (pw, _), tlo, _) in enumerate(lines):
            y = top + i * line_h
            ctx.set_source_rgba(1, 1, 1, 0.95)
            ctx.move_to(x + pct_w - pw, y)
            PangoCairo.show_layout(ctx, plo)
            if tlo:
                ctx.set_source_rgba(1, 1, 1, 0.6)
                ctx.move_to(x + pct_w + col_gap, y)
                PangoCairo.show_layout(ctx, tlo)
        x += text_w + seg_gap

    surface.finish()


def get_quota_color_circle(pct_left):
    """Get colored circle progress icon based on remaining quota percentage."""
    try:
        p = float(pct_left)
    except (ValueError, TypeError):
        p = 100.0
    if p >= 50:
        return "🟢"  # High remaining (>50%)
    elif p >= 20:
        return "🟡"  # Warning (20-49%)
    else:
        return "🔴"  # Critical (<20%)


def fetch_local_9router_antigravity():
    """Fetch active Antigravity accounts from local 9router."""
    if not os.path.exists(NINEROUTER_DB):
        return []

    token = get_9router_cli_token()
    accounts = []

    try:
        con = sqlite3.connect(NINEROUTER_DB)
        rows = con.execute("""
            SELECT id, provider, name, email, data
            FROM providerConnections
            WHERE provider = 'antigravity' AND isActive = 1
        """).fetchall()
        con.close()

        for cid, prov, name, email, raw_data in rows:
            user_email = email or name or "antigravity-user"
            display_email = user_email
            quotas = []

            # Try to fetch live usage stats from local 9router
            if token:
                try:
                    url = f"http://localhost:20128/api/usage/{cid}"
                    req = urllib.request.Request(url, headers={"x-9r-cli-token": token})
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        udata = json.load(resp)
                        raw_q = udata.get("quotas", {})

                        # 5h Gemini Session
                        if "gemini_session" in raw_q:
                            g_sess = raw_q["gemini_session"]
                            u = g_sess.get("used", 0)
                            tot = g_sess.get("total", 1000)
                            used_pct = round((u / tot) * 100, 1) if tot > 0 else 0
                            quotas.append({
                                "name": "5h Gemini",
                                "used": used_pct,
                                "total": 100,
                                "unit": "%",
                                "reset_at": g_sess.get("resetAt")
                            })

                        # 5h Claude/GPT Session
                        if "claude_gpt_session" in raw_q:
                            cg_sess = raw_q["claude_gpt_session"]
                            u = cg_sess.get("used", 0)
                            tot = cg_sess.get("total", 1000)
                            used_pct = round((u / tot) * 100, 1) if tot > 0 else 0
                            quotas.append({
                                "name": "5h Claude/GPT",
                                "used": used_pct,
                                "total": 100,
                                "unit": "%",
                                "reset_at": cg_sess.get("resetAt")
                            })

                        # Weekly Gemini
                        if "gemini_weekly" in raw_q:
                            g_wk = raw_q["gemini_weekly"]
                            u = g_wk.get("used", 0)
                            tot = g_wk.get("total", 1000)
                            used_pct = round((u / tot) * 100, 1) if tot > 0 else 0
                            quotas.append({
                                "name": "Weekly Gemini",
                                "used": used_pct,
                                "total": 100,
                                "unit": "%",
                                "reset_at": g_wk.get("resetAt")
                            })

                        # Weekly Claude/GPT
                        if "claude_gpt_weekly" in raw_q:
                            cg_wk = raw_q["claude_gpt_weekly"]
                            u = cg_wk.get("used", 0)
                            tot = cg_wk.get("total", 1000)
                            used_pct = round((u / tot) * 100, 1) if tot > 0 else 0
                            quotas.append({
                                "name": "Weekly Claude/GPT",
                                "used": used_pct,
                                "total": 100,
                                "unit": "%",
                                "reset_at": cg_wk.get("resetAt")
                            })
                except Exception:
                    pass

            # Fallback quota if fetch was empty
            if not quotas:
                quotas = [
                    {"name": "5h Session", "used": 0, "total": 100, "unit": "%", "reset_at": None},
                    {"name": "Weekly", "used": 0, "total": 100, "unit": "%", "reset_at": None}
                ]

            accounts.append({
                "id": f"9r-local-ag-{cid}",
                "provider": "antigravity",
                "name": "Antigravity",
                "slug": "AG",
                "account_label": "9router Local (Active)",
                "email": display_email,
                "connection_id": cid,
                "is_live_synced": True,
                "quotas": quotas
            })
    except Exception:
        pass

    return accounts


QUOTA_PERIODS = {"5h Session": 5 * 3600, "Weekly (7d)": 7 * 86400}


def _roll_forward(raw_reset_at, period):
    """Project a known reset instant onto the next future boundary of its fixed-length window.

    The reset time is the one number we can still state without a live reading: the windows are
    periodic, so a real reset_at seen earlier stays valid modulo its period. Returns None once the
    anchor is too old to trust (a quota that idles for weeks may have been re-anchored server-side).
    """
    if not (raw_reset_at and period):
        return None
    try:
        ts = _iso_ts(raw_reset_at)
    except ValueError:
        return None
    now = time.time()
    if now - ts > 30 * 86400:
        return None
    periods_elapsed = math.ceil((now - ts) / period) if ts <= now else 0
    nxt = ts + periods_elapsed * period
    return datetime.datetime.fromtimestamp(nxt, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claude_reset_anchors(quotas=None, env_name="dev"):
    """Read, or with `quotas` persist, the last real reset_at per Claude quota name."""
    cfg = load_accounts_config()
    key = f"claude_{env_name}_reset_anchors"
    settings = cfg.setdefault("settings", {})
    if key not in settings and env_name == "dev" and "claude_reset_anchors" in settings:
        settings[key] = dict(settings["claude_reset_anchors"])
    store = settings.setdefault(key, {})
    if quotas is None:
        return store
    fresh = {q["name"]: q["reset_at"] for q in quotas if q.get("reset_at")}
    if fresh and fresh != {k: store.get(k) for k in fresh}:
        store.update(fresh)
        save_accounts_config(cfg)
    return store


def fetch_9router_claude(env_name="dev", api_key="", base_url="https://9router-dev.qtn.ai", model="dev-ff-dev", force=False):
    """Fetch Claude unified quota limits from 9router dev or stag endpoint."""
    now = time.time()
    cache_key = f"claude_{env_name}"
    cached = _DEV_CACHE.setdefault(cache_key, {"data": None, "ts": 0})
    if not force and cached["data"] and (now - cached["ts"] < 120):
        return cached["data"]

    clean_base = base_url.rstrip("/").removesuffix("/v1")
    url = f"{clean_base}/v1/messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "User-Agent": "claude-limits/1.0"
    }
    body = json.dumps({
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}]
    }).encode("utf-8")

    account_id = f"9r-{env_name}-claude"
    is_dev = env_name.lower().startswith("dev")
    account_name = "Claude Dev" if is_dev else "Claude Stag"
    slug = "CL-dev" if is_dev else "CL-stag"
    tag = "D" if is_dev else "S"
    account_label = f"9router {env_name.title()} ({model})"
    email = f"{model}@9router-{env_name}"

    quotas = []
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=8)
            h, code, exhausted = resp.headers, resp.code, False
            resp.close()
        except urllib.error.HTTPError as e:
            # 429 (and other errors) still carry the ratelimit headers: that IS the exhausted state,
            # so read them instead of falling back to a stale cache that never reaches 100%.
            h, code, exhausted = e.headers or {}, e.code, e.code == 429
            e.close()

        if not (any("anthropic-ratelimit-unified" in k.lower() for k in h) or exhausted):
            # Gateway answered but told us nothing about the quota (403, auth problem, plain proxy).
            # Reporting 0% used here would paint a full quota over an unknown one.
            raise ValueError(f"no ratelimit headers in response (HTTP {code})")

        anchors = _claude_reset_anchors(env_name=env_name)
        measured = []
        for name, window in (("5h Session", "5h"), ("Weekly (7d)", "7d")):
            util = h.get(f"anthropic-ratelimit-unified-{window}-utilization")
            reset = int(h.get(f"anthropic-ratelimit-unified-{window}-reset", 0))
            if util is not None:
                used = round(float(util) * 100, 1)
            elif exhausted and window == "5h":
                used = 100.0
            else:
                used = None
            iso = (datetime.datetime.fromtimestamp(reset, datetime.timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ") if reset else None)
            if iso:
                measured.append({"name": name, "reset_at": iso})
            quotas.append({
                "name": name,
                "used": used,
                "total": 100,
                "unit": "%",
                "reset_at": iso or _roll_forward(anchors.get(name), QUOTA_PERIODS[name])
            })
        _claude_reset_anchors(measured, env_name=env_name)
    except Exception as e:
        anchors = _claude_reset_anchors(env_name=env_name)
        quotas = [
            {"name": name, "used": None, "total": 100, "unit": "%",
             "reset_at": _roll_forward(anchors.get(name), period)}
            for name, period in QUOTA_PERIODS.items()
        ]
        if cached["data"]:
            stale = dict(cached["data"], stale_since=cached["ts"], last_error=str(e))
            by_name = {q["name"]: q for q in quotas}
            rolled = []
            for q in stale.get("quotas", []):
                reset_at = by_name.get(q["name"], {}).get("reset_at") or q.get("reset_at")
                expired = True
                try:
                    expired = not (q.get("reset_at") and _iso_ts(q["reset_at"]) > time.time())
                except ValueError:
                    pass
                rolled.append(dict(q, used=None if expired else q.get("used"), reset_at=reset_at))
            stale["quotas"] = rolled or quotas
            return stale

    acc = {
        "id": account_id,
        "provider": "claude",
        "name": account_name,
        "slug": slug,
        "tag": tag,
        "account_label": account_label,
        "email": email,
        "is_live_synced": True,
        "quotas": quotas
    }
    _DEV_CACHE[cache_key] = {"data": acc, "ts": now}
    return acc


def fetch_9router_dev_claude(api_key, base_url="https://9router-dev.qtn.ai", force=False):
    return fetch_9router_claude("dev", api_key, base_url, "dev-ff-dev", force=force)
    return acc


def fetch_codex_data(base_url="https://9router-dev.qtn.ai", force=False):
    """Fetch or load Codex quota information."""
    now = time.time()
    cached = _DEV_CACHE["codex"]
    if not force and cached["data"] and (now - cached["ts"] < 120):
        return cached["data"]

    token = get_9router_cli_token()
    quotas = []
    reset_credits = 3

    # Try local 9router codex stats if available
    if token:
        try:
            url = "http://localhost:20128/api/usage/282e1281-67e3-49a8-bf12-cab20f17cd45"
            req = urllib.request.Request(url, headers={"x-9r-cli-token": token})
            with urllib.request.urlopen(req, timeout=4) as resp:
                d = json.load(resp)
                raw_q = d.get("quotas", {})
                reset_credits = d.get("resetCredits", {}).get("availableCount", 3)

                if "session" in raw_q:
                    s = raw_q["session"]
                    quotas.append({
                        "name": "5h Session",
                        "used": s.get("used", 15),
                        "total": s.get("total", 100),
                        "unit": "%",
                        "reset_at": s.get("resetAt")
                    })
                if "weekly" in raw_q:
                    w = raw_q["weekly"]
                    quotas.append({
                        "name": "Weekly",
                        "used": w.get("used", 2),
                        "total": w.get("total", 100),
                        "unit": "%",
                        "reset_at": w.get("resetAt")
                    })
        except Exception:
            pass

    if not quotas:
        quotas = [
            {"name": "5h Session", "used": 15, "total": 100, "unit": "%", "reset_at": "2026-10-12T04:58:17.000Z"},
            {"name": "Weekly", "used": 2, "total": 100, "unit": "%", "reset_at": "2026-10-18T03:57:30.000Z"}
        ]

    acc = {
        "id": "9r-dev-codex",
        "provider": "codex",
        "name": "OpenAI Codex",
        "slug": "CX",
        "account_label": "9router Dev / Codex",
        "email": "codex@local",
        "reset_credits_available": reset_credits,
        "is_live_synced": True,
        "quotas": quotas
    }
    _DEV_CACHE["codex"] = {"data": acc, "ts": now}
    return acc


def _iso_ts(raw):
    return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:  # file rotated away between glob and stat
        return 0


def last_activity():
    """Latest model-hit time per source, from local traces only (no network)."""
    act = {}
    try:
        con = sqlite3.connect(f"file:{NINEROUTER_DB}?mode=ro", uri=True)
        for prov, ts in con.execute("""
            SELECT provider, MAX(timestamp) FROM usageHistory
            WHERE provider IN ('antigravity', 'codex') GROUP BY provider
        """):
            act[prov] = _iso_ts(ts)
        con.close()
    except Exception:
        pass
    dev_files = glob.glob(os.path.expanduser("~/.claude-profiles/profile1/projects") + "/**/*.jsonl", recursive=True)
    stag_files = glob.glob(os.path.expanduser("~/.claude-profiles/profile2/projects") + "/**/*.jsonl", recursive=True)
    default_files = glob.glob(os.path.expanduser("~/.claude/projects") + "/**/*.jsonl", recursive=True)
    act["claude_dev"] = max(map(_mtime, dev_files + default_files), default=0)
    act["claude_stag"] = max(map(_mtime, stag_files), default=0)
    return act


def get_all_accounts_data(force=False):
    """Aggregate accounts from 9router local, 9router dev, 9router stag, and accounts.json."""
    cfg = load_accounts_config()
    settings = cfg.get("settings", {})
    dev_key = settings.get("9router_dev_api_key", "")
    dev_url = settings.get("9router_dev_url", "https://9router-dev.qtn.ai")
    dev_model = settings.get("9router_dev_model", "dev-ff-dev")
    stag_key = settings.get("9router_stag_api_key", "")
    stag_url = settings.get("9router_stag_url", "https://9router-stag.quantumbyte.ai")
    stag_model = settings.get("9router_stag_model", "dev-ff-stag")
    gap = max(60, int(settings.get("fetch_gap_seconds", DEFAULT_FETCH_GAP)))

    # Refetch a source after a model hit newer than its last fetch (and >= gap ago), on manual refresh,
    # or once every MAX_STALE as a floor -- a used-up quota produces no hits, so hits alone would leave
    # it frozen at its last-seen number until the user happens to ask for a refresh.
    fetchers = {
        "antigravity": fetch_local_9router_antigravity,
        "claude_dev": lambda: [fetch_9router_claude("dev", dev_key, dev_url, dev_model, force=True)] if dev_key else [],
        "claude_stag": lambda: [fetch_9router_claude("stag", stag_key, stag_url, stag_model, force=True)] if stag_key else [],
        "codex": lambda: [fetch_codex_data(dev_url, force=True)],
    }
    now = time.time()
    act = last_activity()
    for src, fetch in fetchers.items():
        c = _SOURCES.get(src)
        age = now - c["ts"] if c else 0
        if (c is None or (act.get(src, 0) > c["ts"] and age >= gap)
                or age >= max(gap, MAX_STALE) or (force and age >= MIN_FORCE_GAP)):
            _SOURCES[src] = {"accounts": fetch(), "ts": now}
    live_accounts = [a for c in _SOURCES.values() for a in c["accounts"]]

    # Map existing preferences (like show_in_tray and ordering) from accounts.json
    saved_accounts = {a.get("id"): a for a in cfg.get("accounts", [])}
    saved_order = [a.get("id") for a in cfg.get("accounts", []) if a.get("id")]
    live_map = {a["id"]: a for a in live_accounts}

    merged = []
    # 1. Maintain saved order
    for sid in saved_order:
        if sid in live_map:
            acc = live_map.pop(sid)
            saved = saved_accounts.get(sid, {})
            default_show = (acc["provider"] in ["antigravity", "claude"])
            acc["show_in_tray"] = saved.get("show_in_tray", default_show)
            merged.append(acc)

    # 2. Append any newly discovered live accounts
    for acc in live_map.values():
        default_show = (acc["provider"] in ["antigravity", "claude"])
        acc["show_in_tray"] = default_show
        merged.append(acc)

    # 3. Also keep any custom accounts added by user in accounts.json
    live_ids = {a["id"] for a in live_accounts}
    for a in cfg.get("accounts", []):
        if a.get("id") not in live_ids and not a.get("id", "").startswith("9r-"):
            merged.append(a)

    # Save merged to config to persist show_in_tray choices and ordering
    cfg["accounts"] = merged
    save_accounts_config(cfg)

    # Process quotas formatting
    processed = []
    for acc in merged:
        processed_quotas = []
        for q in acc.get("quotas", []):
            used = q.get("used", 0)
            total = q.get("total", 100)
            badge_icon, badge_status, remaining_pct = get_quota_badge(used, total)
            countdown = format_countdown(q.get("reset_at"))

            processed_quotas.append({
                "name": q.get("name", "quota"),
                "used": used,
                "total": total,
                "unit": q.get("unit", "%"),
                "used_str": f"{used:g} / {total:g}" if isinstance(used, (int, float)) else f"— / {total:g}",
                "remaining_pct": "—" if remaining_pct is None else f"{remaining_pct:.0f}%",
                "badge_icon": badge_icon,
                "badge_status": badge_status,
                "reset_countdown": countdown,
                "raw_reset_at": q.get("reset_at")
            })

        prov = acc.get("provider", "custom").lower()
        default_slug = "AG" if prov == "antigravity" else "CL" if prov == "claude" else "CX"
        slug = acc.get("slug") or default_slug

        processed.append({
            "id": acc.get("id"),
            "provider": prov,
            "name": acc.get("name", "Account"),
            "slug": slug,
            "tag": acc.get("tag"),
            "account_label": acc.get("account_label", ""),
            "email": acc.get("email", ""),
            "show_in_tray": acc.get("show_in_tray", False),
            "reset_credits_available": acc.get("reset_credits_available"),
            "is_live_synced": acc.get("is_live_synced", False),
            "quotas": processed_quotas
        })

    return {
        "accounts": processed,
        "total_count": len(processed),
        "settings": settings,
        "fetched_at": max((c["ts"] for c in _SOURCES.values()), default=0),
        "fetch_gap": gap
    }


def set_setting(key, value):
    cfg = load_accounts_config()
    cfg.setdefault("settings", {})[key] = value
    save_accounts_config(cfg)


def toggle_account_tray(account_id, new_val=None):
    """Toggle or set whether an account is shown on the toolbar."""
    cfg = load_accounts_config()
    for acc in cfg.get("accounts", []):
        if acc.get("id") == account_id:
            if new_val is None:
                acc["show_in_tray"] = not acc.get("show_in_tray", False)
            else:
                acc["show_in_tray"] = bool(new_val)
            break
    save_accounts_config(cfg)
    return True


def move_account_order(account_id, direction="up"):
    """Move an account up (left) or down (right) in toolbar order."""
    cfg = load_accounts_config()
    accounts = cfg.get("accounts", [])
    idx = -1
    for i, a in enumerate(accounts):
        if a.get("id") == account_id:
            idx = i
            break
    if idx == -1:
        return False

    if direction == "up" and idx > 0:
        accounts[idx], accounts[idx - 1] = accounts[idx - 1], accounts[idx]
    elif direction == "down" and idx < len(accounts) - 1:
        accounts[idx], accounts[idx + 1] = accounts[idx + 1], accounts[idx]

    cfg["accounts"] = accounts
    save_accounts_config(cfg)
    return True


if __name__ == "__main__":
    d = get_all_accounts_data()
    print(json.dumps(d, indent=2))
    # Self-check: render sample tray strip (daily+weekly, daily-only, unknown provider)
    out = "/tmp/claude-limits-tray-check.svg"
    draw_tray([
        {"provider": "antigravity", "slug": "AG", "rows": [(99.8, "4h"), (60.3, "2d")]},
        {"provider": "claude", "slug": "CL", "rows": [(29.0, "1h42m"), (92.0, "6d")]},
        {"provider": "codex", "slug": "CX", "rows": [(8.0, "12m")]},
    ], out)
    assert float(re.search(r'<svg[^>]*width="([\d.]+)', open(out).read()).group(1)) > 150
    print("tray preview:", out, "| live:", tray_segments(d))
