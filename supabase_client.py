"""
Supabase persistence layer for Ozark LTC Rx Cycle Tracker.

Tables expected in Supabase:
  - tracking_state: id (int8, PK), key (text, unique), value (jsonb)
  - users_config: id (int8, PK), key (text, unique), value (jsonb)
  - audit_logs: id (int8, PK), facility (text), stage (text), initials (text),
                logged_at (timestamptz), date_str (text), time_str (text)

Falls back to local JSON/YAML files when SUPABASE_URL or SUPABASE_KEY are not set.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_supabase_client = None
_USE_SUPABASE = False

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
SHARED_STATE_FILE = DATA_DIR / "shared_tracking_state.json"
FACILITIES_FILE = DATA_DIR / "facilities.json"

try:
    from supabase import create_client, Client
    _url = os.environ.get("SUPABASE_URL", "")
    _key = os.environ.get("SUPABASE_KEY", "")
    if _url and _key:
        _supabase_client = create_client(_url, _key)
        _USE_SUPABASE = True
except ImportError:
    pass


def using_supabase() -> bool:
    return _USE_SUPABASE


def get_client() -> "Client | None":
    return _supabase_client


# ── Tracking State ──────────────────────────────────────────────────────────

def load_tracking_state() -> dict:
    """Load shared tracking state (cycle_team_tracking, dollar_tracking, etc.)."""
    default = {
        "cycle_team_tracking": {},
        "dollar_tracking": {},
        "unlocked_days": [],
        "dollar_unlocked_days": [],
    }
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "shared").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default

    # Fallback: local JSON
    if SHARED_STATE_FILE.exists():
        try:
            return json.loads(SHARED_STATE_FILE.read_text())
        except Exception:
            pass
    return default


def save_tracking_state(state: dict) -> None:
    """Persist shared tracking state."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "shared", "value": state},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass

    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SHARED_STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Facilities Config ───────────────────────────────────────────────────────

def load_facilities_config_db() -> dict[str, list[dict]] | None:
    """Load facility schedule from Supabase. Returns None if not using Supabase or not found."""
    if not _USE_SUPABASE:
        return None
    try:
        resp = _supabase_client.table("tracking_state").select("value").eq("key", "facilities").execute()
        if resp.data:
            return resp.data[0]["value"]
    except Exception:
        pass
    return None


def save_facilities_config_db(config: dict[str, list[dict]]) -> None:
    """Save facility schedule to Supabase."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "facilities", "value": config},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass
    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FACILITIES_FILE.write_text(json.dumps(config, indent=2))


# ── Audit Logs ──────────────────────────────────────────────────────────────

def log_audit_entry(facility: str, stage: str, initials: str) -> None:
    """Write an audit log entry (stage completion)."""
    now = datetime.now()
    if _USE_SUPABASE:
        try:
            _supabase_client.table("audit_logs").insert({
                "facility": facility,
                "stage": stage,
                "initials": initials,
                "logged_at": now.isoformat(),
                "date_str": now.strftime("%Y-%m-%d"),
                "time_str": now.strftime("%H:%M:%S"),
            }).execute()
            return
        except Exception:
            pass
    # Fallback handled by the existing Excel logger in app.py


def get_facility_completion_times(facility: str, stage: str = "Facility finished", weeks: int = 4) -> list[datetime]:
    """Get completion timestamps for a facility over the last N weeks.

    Returns list of datetime objects when the given stage was completed.
    """
    if not _USE_SUPABASE:
        return []
    try:
        cutoff = (datetime.now() - timedelta(weeks=weeks)).isoformat()
        resp = (
            _supabase_client.table("audit_logs")
            .select("logged_at")
            .eq("facility", facility)
            .eq("stage", stage)
            .gte("logged_at", cutoff)
            .order("logged_at", desc=True)
            .execute()
        )
        times = []
        for row in resp.data:
            try:
                times.append(datetime.fromisoformat(row["logged_at"]))
            except (ValueError, TypeError):
                pass
        return times
    except Exception:
        return []


def get_average_completion_hour(facility: str, stage: str = "Facility finished", weeks: int = 4) -> float | None:
    """Compute average hour-of-day (as float, e.g. 14.5 = 2:30 PM) when a facility
    completes the given stage, over the last N weeks.

    Returns None if no data.
    """
    times = get_facility_completion_times(facility, stage, weeks)
    if not times:
        return None
    total_minutes = sum(t.hour * 60 + t.minute for t in times)
    avg_minutes = total_minutes / len(times)
    return avg_minutes / 60.0


# ── Users Config (for Supabase-backed auth) ─────────────────────────────────

def load_users_config_db() -> dict | None:
    """Load user/auth config from Supabase. Returns None if not using Supabase."""
    if not _USE_SUPABASE:
        return None
    try:
        resp = _supabase_client.table("users_config").select("value").eq("key", "auth").execute()
        if resp.data:
            return resp.data[0]["value"]
    except Exception:
        pass
    return None


def save_users_config_db(config: dict) -> None:
    """Save user/auth config to Supabase."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("users_config").upsert(
                {"key": "auth", "value": config},
                on_conflict="key",
            ).execute()
        except Exception:
            pass


# ── Full Auth Config (credentials + permissions + cookie settings) ─────────

def load_auth_config_db() -> dict | None:
    """Load full auth config from Supabase. Returns None if not available.
    
    This includes credentials, permissions, and cookie settings - everything
    needed for streamlit_authenticator to work.
    """
    if not _USE_SUPABASE:
        return None
    try:
        resp = _supabase_client.table("users_config").select("value").eq("key", "auth_config").execute()
        if resp.data:
            return resp.data[0]["value"]
    except Exception:
        pass
    return None


def save_auth_config_db(config: dict) -> None:
    """Save full auth config to Supabase for persistence across restarts.
    
    This is the key function that ensures user accounts persist on Streamlit Cloud.
    """
    if _USE_SUPABASE:
        try:
            _supabase_client.table("users_config").upsert(
                {"key": "auth_config", "value": config},
                on_conflict="key",
            ).execute()
        except Exception as e:
            print(f"Warning: Failed to save auth config to Supabase: {e}")


# ── Bag Count Tracking ──────────────────────────────────────────────────────

BAG_COUNT_FILE = DATA_DIR / "bag_count_state.json"

def load_bag_count_state() -> dict:
    """Load bag count tracking state.
    
    Structure:
    {
        "batches": {
            "Facility Name": [
                {"name": "Batch A", "id": "uuid"},
                {"name": "Batch B", "id": "uuid"}
            ]
        },
        "counts": {
            "Mon": {
                "Facility Name": {
                    "batch_id": {"bags": 10, "census": 45},
                    ...
                }
            }
        },
        "unlocked_days": [],
        "completed_days": []
    }
    """
    default = {
        "batches": {},
        "counts": {},
        "unlocked_days": [],
        "completed_days": [],
    }
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "bag_counts").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default

    # Fallback: local JSON
    if BAG_COUNT_FILE.exists():
        try:
            return json.loads(BAG_COUNT_FILE.read_text())
        except Exception:
            pass
    return default


def save_bag_count_state(state: dict) -> None:
    """Persist bag count tracking state."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "bag_counts", "value": state},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass

    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BAG_COUNT_FILE.write_text(json.dumps(state, indent=2))
