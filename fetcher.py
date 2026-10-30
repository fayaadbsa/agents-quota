#!/usr/bin/env python3
import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error
import sqlite3

CONFIG_DIR = os.path.expanduser("~/.config/claude-limits")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
NINEROUTER_DB = os.path.expanduser("~/.9router/db/data.sqlite")


def discover_profiles():
    """Auto-discover all clauded / claude-profiles on the system."""
    home = os.path.expanduser("~")
    profiles = {}

    # Default profile (clauded)
    default_dir = os.path.join(home, ".claude")
    if os.path.exists(default_dir):
        profiles["clauded"] = {
            "name": "clauded (Default)",
            "path": default_dir,
            "type": "default"
        }

    # Custom profiles in ~/.claude-profiles/ (clauded1, clauded2, clauded3, etc.)
    profiles_root = os.path.join(home, ".claude-profiles")
    if os.path.exists(profiles_root):
        for entry in sorted(os.listdir(profiles_root)):
            entry_path = os.path.join(profiles_root, entry)
            if os.path.isdir(entry_path):
                key = entry.replace("profile", "clauded")
                if not key.startswith("clauded"):
                    key = f"clauded-{entry}"
                profiles[key] = {
                    "name": f"{key} ({entry})",
                    "path": entry_path,
                    "type": entry
                }

    return profiles


def get_profile_details(profile_key):
    profiles = discover_profiles()
    p_info = profiles.get(profile_key)
    if not p_info:
        p_info = profiles.get("clauded", {
            "name": "clauded",
            "path": os.path.expanduser("~/.claude"),
            "type": "default"
        })

    p_dir = p_info["path"]
    settings_file = os.path.join(p_dir, "settings.json")
    creds_file = os.path.join(p_dir, ".credentials.json")

    base_url = ""
    auth_token = ""
    oauth_token = ""
    oauth_tier = ""

    # Check settings.json
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r") as f:
                sdata = json.load(f)
                env = sdata.get("env", {})
                base_url = env.get("ANTHROPIC_BASE_URL", "")
                auth_token = env.get("ANTHROPIC_AUTH_TOKEN", "")
        except Exception:
            pass

    # Check .credentials.json
    if os.path.exists(creds_file):
        try:
            with open(creds_file, "r") as f:
                cdata = json.load(f)
                oauth = cdata.get("claudeAiOauth", {})
                oauth_token = oauth.get("accessToken", "")
                raw_tier = oauth.get("rateLimitTier", "")
                if raw_tier:
                    oauth_tier = raw_tier.replace("default_claude_", "").replace("_", " ").title()
        except Exception:
            pass

    return {
        **p_info,
        "base_url": base_url,
        "auth_token": auth_token,
        "oauth_token": oauth_token,
        "oauth_tier": oauth_tier
    }


def format_tokens(num):
    if num is None:
        return "0"
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def format_reset_time(iso_or_ts):
    if not iso_or_ts:
        return "N/A"
    try:
        if isinstance(iso_or_ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(iso_or_ts)
        else:
            dt = datetime.datetime.fromisoformat(str(iso_or_ts).replace("Z", "+00:00")).astimezone()
        now = datetime.datetime.now(dt.tzinfo)
        diff = dt - now
        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "Just now"

        hours, rem = divmod(total_seconds, 3600)
        minutes = rem // 60
        if hours > 24:
            days = hours // 24
            return f"{days}d {hours % 24}h ({dt.strftime('%b %d, %H:%M')})"
        elif hours > 0:
            return f"{hours}h {minutes}m ({dt.strftime('%H:%M')})"
        else:
            return f"{minutes}m ({dt.strftime('%H:%M')})"
    except Exception:
        return str(iso_or_ts)


def fetch_from_9router_db():
    """Read actual live stats from local 9router SQLite database."""
    if not os.path.exists(NINEROUTER_DB):
        return None

    try:
        con = sqlite3.connect(NINEROUTER_DB)
        cur = con.cursor()

        now_ms = int(time.time() * 1000)
        five_h_ago = now_ms - (5 * 3600 * 1000)
        seven_d_ago = now_ms - (7 * 24 * 3600 * 1000)

        # 5-hour window
        cur.execute("""
            SELECT COUNT(*), SUM(promptTokens), SUM(completionTokens), SUM(cost)
            FROM usageHistory
            WHERE timestamp >= ?
        """, (five_h_ago,))
        c5, pt5, ct5, cost5 = cur.fetchone()

        # 7-day window
        cur.execute("""
            SELECT COUNT(*), SUM(promptTokens), SUM(completionTokens), SUM(cost)
            FROM usageHistory
            WHERE timestamp >= ?
        """, (seven_d_ago,))
        c7, pt7, ct7, cost7 = cur.fetchone()

        # Active providers
        active_rows = cur.execute("""
            SELECT id, provider, name, email, data
            FROM providerConnections
            WHERE isActive=1
        """).fetchall()

        providers_list = []
        for r in active_rows:
            p_id, prov, name, email, raw_data = r
            p_data = json.loads(raw_data) if raw_data else {}
            rate_limited_until = p_data.get("rateLimitedUntil")
            is_rl = bool(rate_limited_until and rate_limited_until > now_ms)
            providers_list.append({
                "provider": prov,
                "name": name,
                "email": email,
                "is_rate_limited": is_rl
            })

        con.close()

        total_5h_tokens = (pt5 or 0) + (ct5 or 0)
        total_7d_tokens = (pt7 or 0) + (ct7 or 0)

        return {
            "source": "9router_local_db",
            "reqs_5h": c5 or 0,
            "tokens_5h": total_5h_tokens,
            "cost_5h": cost5 or 0.0,
            "reqs_7d": c7 or 0,
            "tokens_7d": total_7d_tokens,
            "cost_7d": cost7 or 0.0,
            "active_providers": providers_list
        }
    except Exception as e:
        return None


def fetch_from_claude_oauth(oauth_token):
    """Fetch live data from official Claude OAuth endpoint."""
    if not oauth_token:
        return None

    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": f"Bearer {oauth_token}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
            five_hour_raw = data.get("five_hour") or data.get("session") or {}
            weekly_raw = data.get("seven_day") or data.get("weekly") or {}

            five_util = five_hour_raw.get("utilization", 0)
            if five_util <= 1 and isinstance(five_util, float):
                five_util = round(five_util * 100, 1)

            week_util = weekly_raw.get("utilization", 0)
            if week_util <= 1 and isinstance(week_util, float):
                week_util = round(week_util * 100, 1)

            return {
                "source": "claude_oauth",
                "five_hour": {
                    "utilization": five_util,
                    "reset_str": format_reset_time(five_hour_raw.get("resets_at")),
                },
                "weekly": {
                    "utilization": week_util,
                    "reset_str": format_reset_time(weekly_raw.get("resets_at")),
                }
            }
    except Exception:
        return None


def load_config():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        default_config = {
            "active_profile": "clauded",
            "poll_interval_seconds": 120
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(default_config, f, indent=2)
        return default_config

    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"active_profile": "clauded"}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def fetch_usage(target_profile=None):
    config = load_config()
    active_prof = target_profile or config.get("active_profile", "clauded")
    p_info = get_profile_details(active_prof)
    all_discovered = discover_profiles()

    base_url = p_info.get("base_url", "")
    oauth_token = p_info.get("oauth_token", "")

    # Case 1: Official Claude OAuth
    if oauth_token:
        oauth_res = fetch_from_claude_oauth(oauth_token)
        if oauth_res:
            return {
                "status": "configured",
                "type": "claude_oauth",
                "active_profile": active_prof,
                "all_profiles": list(all_discovered.keys()),
                "tier": p_info.get("oauth_tier") or "Official Claude Pro/Team",
                "five_hour": oauth_res["five_hour"],
                "weekly": oauth_res["weekly"],
                "fetched_at": time.time(),
                "error": None
            }

    # Case 2: Local 9router (localhost:20128)
    if "20128" in base_url or active_prof in ["clauded", "clauded3"]:
        db_data = fetch_from_9router_db()
        if db_data:
            return {
                "status": "configured",
                "type": "9router_local",
                "active_profile": active_prof,
                "all_profiles": list(all_discovered.keys()),
                "tier": "9router Local (Port 20128)",
                "base_url": base_url or "http://localhost:20128/v1",
                "stats_5h": {
                    "reqs": db_data["reqs_5h"],
                    "tokens_str": format_tokens(db_data["tokens_5h"]),
                    "cost_str": f"${db_data['cost_5h']:.4f}",
                },
                "stats_7d": {
                    "reqs": db_data["reqs_7d"],
                    "tokens_str": format_tokens(db_data["tokens_7d"]),
                    "cost_str": f"${db_data['cost_7d']:.4f}",
                },
                "active_providers": db_data["active_providers"],
                "fetched_at": time.time(),
                "error": None
            }

    # Case 3: Remote 9router (e.g. 9router-dev, 9router-stag)
    if base_url:
        return {
            "status": "not_yet_set",
            "type": "remote_gateway",
            "active_profile": active_prof,
            "all_profiles": list(all_discovered.keys()),
            "tier": f"Remote Gateway ({active_prof})",
            "base_url": base_url,
            "message": "Remote 9router endpoint. Quota API is behind dashboard web auth.",
            "fetched_at": time.time(),
            "error": "Not yet set / No public quota API for remote router"
        }

    # Case 4: Not configured
    return {
        "status": "not_yet_set",
        "type": "unconfigured",
        "active_profile": active_prof,
        "all_profiles": list(all_discovered.keys()),
        "tier": "Not Configured",
        "message": "Profile has no active OAuth credentials or local 9router connection.",
        "fetched_at": time.time(),
        "error": "Not yet set"
    }


if __name__ == "__main__":
    prof = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(fetch_usage(target_profile=prof), indent=2))
