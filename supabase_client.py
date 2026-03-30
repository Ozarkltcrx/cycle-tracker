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


# ── Master Facility List (Pharmacy Management) ──────────────────────────────

def load_master_facilities() -> list[dict]:
    """Load master facility list from Supabase.
    
    Each facility has:
    - name: str
    - start_date: str (YYYY-MM-DD)
    - original_term: int (years)
    - renewal_term: int (years)
    """
    default = []
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "master_facilities").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default
    
    # Fallback: local JSON
    master_file = DATA_DIR / "master_facilities.json"
    if master_file.exists():
        try:
            return json.loads(master_file.read_text())
        except Exception:
            pass
    return default


def save_master_facilities(facilities: list[dict]) -> None:
    """Save master facility list to Supabase."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "master_facilities", "value": facilities},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass
    
    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    master_file = DATA_DIR / "master_facilities.json"
    master_file.write_text(json.dumps(facilities, indent=2))


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


# ── BNDD License Tracking ───────────────────────────────────────────────────

BNDD_FILE = DATA_DIR / "bndd_licenses.json"

def load_bndd_licenses() -> list[dict]:
    """Load BNDD license data.
    
    Each license has:
    - facility: str
    - license_number: str
    - expiration_date: str (YYYY-MM-DD)
    """
    default = []
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "bndd_licenses").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default
    
    # Fallback: local JSON
    if BNDD_FILE.exists():
        try:
            return json.loads(BNDD_FILE.read_text())
        except Exception:
            pass
    return default


def save_bndd_licenses(licenses: list[dict]) -> None:
    """Save BNDD license data."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "bndd_licenses", "value": licenses},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass
    
    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BNDD_FILE.write_text(json.dumps(licenses, indent=2))


# ── Cubex Re-Stock Tracking ─────────────────────────────────────────────────

CUBEX_FILE = DATA_DIR / "cubex_restock.json"

def load_cubex_restock() -> list[dict]:
    """Load Cubex re-stock data.
    
    Each entry has:
    - facility: str
    - serial_number: str
    - restock_date: str (YYYY-MM-DD)
    - next_restock_due: str (YYYY-MM-DD) - always 11 months after restock_date
    """
    default = []
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "cubex_restock").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default
    
    # Fallback: local JSON
    if CUBEX_FILE.exists():
        try:
            return json.loads(CUBEX_FILE.read_text())
        except Exception:
            pass
    return default


def save_cubex_restock(entries: list[dict]) -> None:
    """Save Cubex re-stock data."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "cubex_restock", "value": entries},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass
    
    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CUBEX_FILE.write_text(json.dumps(entries, indent=2))


# ── Pharmacy Licenses Tracking ──────────────────────────────────────────────

PHARMACY_LICENSES_FILE = DATA_DIR / "pharmacy_licenses.json"

def load_pharmacy_licenses() -> list[dict]:
    """Load pharmacy licenses data.
    
    Each entry has:
    - facility: str
    - license_number: str
    - license_date: str (YYYY-MM-DD)
    - expiration: str (YYYY-MM-DD)
    """
    default = []
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "pharmacy_licenses").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default
    
    # Fallback: local JSON
    if PHARMACY_LICENSES_FILE.exists():
        try:
            return json.loads(PHARMACY_LICENSES_FILE.read_text())
        except Exception:
            pass
    return default


def save_pharmacy_licenses(licenses: list[dict]) -> None:
    """Save pharmacy licenses data."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "pharmacy_licenses", "value": licenses},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass
    
    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PHARMACY_LICENSES_FILE.write_text(json.dumps(licenses, indent=2))


# ── Delivery Routes Tracking ────────────────────────────────────────────────

DELIVERY_ROUTES_FILE = DATA_DIR / "delivery_routes.json"

def load_delivery_routes() -> dict:
    """Load delivery routes data.
    
    Structure:
    {
        "AM": [{"name": "Route 1", "facilities": ["Fac A", "Fac B"], "departure_time": "08:00"}, ...],
        "PM": [...],
        "Weekend": [...]
    }
    """
    default = {"AM": [], "PM": [], "Weekend": []}
    if _USE_SUPABASE:
        try:
            resp = _supabase_client.table("tracking_state").select("value").eq("key", "delivery_routes").execute()
            if resp.data:
                return resp.data[0]["value"]
        except Exception:
            pass
        return default
    
    # Fallback: local JSON
    if DELIVERY_ROUTES_FILE.exists():
        try:
            return json.loads(DELIVERY_ROUTES_FILE.read_text())
        except Exception:
            pass
    return default


def save_delivery_routes(routes: dict) -> None:
    """Save delivery routes data."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("tracking_state").upsert(
                {"key": "delivery_routes", "value": routes},
                on_conflict="key",
            ).execute()
            return
        except Exception:
            pass
    
    # Fallback: local JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DELIVERY_ROUTES_FILE.write_text(json.dumps(routes, indent=2))


def export_and_reset_bag_counts(email_to: str = "acheeley@ozarkltcrx.com") -> str:
    """Export current week's bag counts (Mon-Fri only) to CSV, email it, and reset for new week.
    
    Called by Sunday 7pm cron job. Only exports Mon-Fri data, ignores any
    'next Monday' data that might have been entered early on Friday.
    
    Args:
        email_to: Email address to send the report to.
    
    Returns: Status message.
    """
    import csv
    import subprocess
    from datetime import datetime
    
    state = load_bag_count_state()
    counts = state.get("counts", {})
    batches = state.get("batches", {})
    
    # Only export Mon-Fri (current week)
    WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    
    # Get week info for filename
    today = datetime.now()
    week_num = today.isocalendar()[1]
    year = today.year
    filename = f"bag_counts_{year}_W{week_num:02d}.csv"
    filepath = APP_DIR / "data" / filename
    
    # Build export rows
    rows = []
    for day in WEEKDAYS:
        day_counts = counts.get(day, {})
        for facility, fac_counts in day_counts.items():
            fac_batches = batches.get(facility, [])
            batch_lookup = {b["id"]: b["name"] for b in fac_batches}
            for batch_id, values in fac_counts.items():
                batch_name = batch_lookup.get(batch_id, batch_id)
                rows.append({
                    "day": day,
                    "facility": facility,
                    "batch": batch_name,
                    "bags": values.get("bags", 0),
                    "census": values.get("census", 0),
                })
    
    result_msg = ""
    
    if rows:
        # Write CSV
        (APP_DIR / "data").mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["day", "facility", "batch", "bags", "census"])
            writer.writeheader()
            writer.writerows(rows)
        
        # Calculate totals for email summary
        total_bags = sum(r["bags"] or 0 for r in rows)
        total_census = sum(r["census"] or 0 for r in rows)
        facilities = len(set(r["facility"] for r in rows))
        
        # Email the report
        subject = f"Weekly Bag Count Report - Week {week_num}, {year}"
        body = f"""Weekly Bag Count Report

Week {week_num}, {year}

Summary:
- Total Bags: {total_bags}
- Total Census: {total_census}
- Facilities: {facilities}
- Data Points: {len(rows)}

The detailed CSV report is attached.

---
Automated report from Ozark LTC Rx Cycle Tracker
"""
        
        try:
            # Send email with attachment using gog
            cmd = [
                "gog", "gmail", "send",
                "--to", email_to,
                "--subject", subject,
                "--body", body,
                "--attach", str(filepath)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            result_msg = f"Exported {len(rows)} rows to {filename} and emailed to {email_to}."
        except subprocess.CalledProcessError as e:
            result_msg = f"Exported {len(rows)} rows to {filename} but email failed: {e}"
        except Exception as e:
            result_msg = f"Exported {len(rows)} rows to {filename} but email failed: {e}"
    else:
        result_msg = "No bag count data to export."
    
    # Reset state for new week - clear counts and completed_days, keep batches
    new_state = {
        "batches": batches,  # Keep batch definitions
        "counts": {},        # Clear all counts
        "unlocked_days": [], # Reset unlocks
        "completed_days": [], # Reset completions
    }
    save_bag_count_state(new_state)
    
    return result_msg + " Reset for new week."
