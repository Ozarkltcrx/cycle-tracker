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


def export_and_reset_bag_counts(email_to: str = "acheeley@ozarkltcrx.com", reset: bool = True) -> str:
    """Export current week's bag counts to Excel using template with formatting preserved.
    
    Uses the template file (data/template_bag_count.xlsx) as base and fills in data values.
    Preserves all borders, highlighting, and cell formatting from the template.
    
    Args:
        email_to: Email address to send the report to.
        reset: If True, reset counts after export (for Sunday cron). If False, keep data.
    
    Returns: Status message.
    """
    import subprocess
    import shutil
    from datetime import datetime
    from zipfile import ZIP_DEFLATED, ZipFile
    import urllib.request
    import re
    
    # Load directly from Supabase to avoid module-level init issues
    _url = os.environ.get("SUPABASE_URL", "")
    _key = os.environ.get("SUPABASE_KEY", "")
    
    if _url and _key:
        try:
            req = urllib.request.Request(
                f"{_url}/rest/v1/tracking_state?key=eq.bag_counts",
                headers={"apikey": _key, "Authorization": f"Bearer {_key}"}
            )
            with urllib.request.urlopen(req) as resp:
                import json as _json
                data = _json.loads(resp.read())
                state = data[0]["value"] if data else {}
        except Exception:
            state = load_bag_count_state()
    else:
        state = load_bag_count_state()
    
    counts = state.get("counts", {})
    batches_config = state.get("batches", {})
    
    # Cell mapping: Maps (facility_key, batch_name) -> value_cell_ref
    # Based on template layout - value column is D for Mon, H for Tue, L for Wed, P for Thu, T for Fri
    CELL_MAP = {
        # Monday (value col D)
        ("Mother of Good Counsel", "1st Floor"): "D4",
        ("Mother of Good Counsel", "2nd Floor"): "D6",
        ("Mother of Good Counsel", "3rd Floor"): "D7",
        ("McClay", "100 Hall"): "D10",
        ("McClay", "L Hall"): "D11",
        ("Belleview", "Hall A"): "D14",
        ("Belleview", "Hall B"): "D15",
        ("Belleview", "Unit"): "D16",
        ("Hillside", "Hill 1"): "D19",
        ("Hillside", "Hill 2"): "D20",
        ("Superior Manor", "Hall 100"): "D23",
        ("Superior Manor", "Hall 200"): "D24",
        ("Superior Manor", "Hall 300"): "D25",
        ("Superior Manor", "Hall 400"): "D26",
        ("Walnut Street", "1"): "D29",
        ("Colonial Doniphan", "Upstairs"): "D31",
        ("Colonial Doniphan", "Down"): "D32",
        ("New Hope", "ALF"): "D35",
        ("New Hope", "ILF"): "D36",
        # Tuesday (value col H)
        ("Westwood Hills", "A Wing"): "H4",
        ("Westwood Hills", "B Wing"): "H5",
        ("Westwood Hills", "C Wing"): "H6",
        ("Glenfield", "100"): "H9",
        ("Granite House", "1"): "H12",
        ("Creve Coeur", "1"): "H14",
        ("Creve Coeur", "2"): "H15",
        # Wednesday (value col L)
        ("Licking RCF", "1"): "L4",
        ("Salem Care Center", "1"): "L6",
        ("Salem Care Center", "2"): "L7",
        ("Salem Care Center", "3"): "L8",
        ("Salem Residential Care", "RCF"): "L10",
        ("Baisch SNF", "1"): "L12",
        ("Baisch SNF", "2"): "L13",
        ("Baisch RCF", "RCF"): "L14",
        ("Pillars", "1"): "L17",
        ("Pillars", "2"): "L19",
        ("Legacy", "1"): "L23",
        ("John Knox CSL", "CSL"): "L25",
        ("John Knox MM", "MM"): "L26",
        ("John Knox ALF", "ALF"): "L27",
        ("John Knox ILF", "ILF 1"): "L28",
        ("John Knox ILF", "ILF 2"): "L29",
        # Thursday (value col P)
        ("Delta South", "1"): "P4",
        ("Delta South", "2"): "P6",
        ("Seville", "1"): "P10",
        ("St Johns", "1"): "P12",
        ("St Johns", "2"): "P13",
        ("UCity", "1"): "P16",
        ("UCity", "2"): "P17",
        ("UCity", "3"): "P18",
        ("UCity", "4"): "P19",
        ("UCity", "5"): "P20",
        ("Bentley's", "1"): "P22",  # After U-City
        # Friday (value col T)
        ("Colonial Bismark", "1"): "T4",
        ("Bertrand", "1"): "T6",
        ("Bertrand", "2"): "T8",
        ("Oakdale SNF", "1"): "T12",
        ("Oakdale SNF", "2"): "T14",
        ("Oakdale ALF", "1"): "T18",
        ("Oakdale ALF", "2"): "T19",
        ("Oakdale ALF", "3"): "T20",
        ("Oakdale RCF", "1"): "T23",
        ("Oakdale RCF", "2"): "T24",
    }
    
    # Daily total cells (for summing)
    DAILY_TOTAL_CELLS = {
        "Mon": "D39",
        "Tue": "H18",
        "Wed": "L32",
        "Thu": "P23",
        "Fri": "T27",
    }
    
    # Facility total cells (sum row after each facility)
    FACILITY_TOTAL_MAP = {
        # Monday
        ("Mother of Good Counsel", "Mon"): "D8",
        ("McClay", "Mon"): "D12",
        ("Belleview", "Mon"): "D17",
        ("Hillside", "Mon"): "D21",
        ("Superior Manor", "Mon"): "D27",
        ("Walnut Street", "Mon"): "D29",  # Single batch, no separate total
        ("Colonial Doniphan", "Mon"): "D33",
        ("New Hope", "Mon"): "D37",
        # Tuesday
        ("Westwood Hills", "Tue"): "H7",
        ("Glenfield", "Tue"): "H10",
        ("Granite House", "Tue"): "H12",  # Single batch
        ("Creve Coeur", "Tue"): "H16",
        # Wednesday
        ("Licking RCF", "Wed"): "L4",  # Single batch
        ("Salem Care Center", "Wed"): "L8",  # After 3 batches
        ("Salem Residential Care", "Wed"): "L10",  # Single
        ("Baisch SNF", "Wed"): "L13",  # After 2
        ("Baisch RCF", "Wed"): "L14",  # Single
        ("Pillars", "Wed"): "L21",  # After 2
        ("Legacy", "Wed"): "L23",  # Single
        ("John Knox CSL", "Wed"): "L25",  # Single
        ("John Knox MM", "Wed"): "L26",  # Single
        ("John Knox ALF", "Wed"): "L27",  # Single
        ("John Knox ILF", "Wed"): "L30",  # After 2
        # Thursday
        ("Delta South", "Thu"): "P8",
        ("Seville", "Thu"): "P10",
        ("St Johns", "Thu"): "P14",
        ("UCity", "Thu"): "P21",
        ("Bentley's", "Thu"): "P22",
        # Friday
        ("Colonial Bismark", "Fri"): "T4",
        ("Bertrand", "Fri"): "T10",
        ("Oakdale SNF", "Fri"): "T16",
        ("Oakdale ALF", "Fri"): "T21",
        ("Oakdale RCF", "Fri"): "T25",
    }
    
    # Get week info
    today = datetime.now()
    week_num = today.isocalendar()[1]
    year = today.year
    filename = f"Cycle_Bag_Count_{year}_W{week_num:02d}.xlsx"
    filepath = APP_DIR / "data" / filename
    template_path = APP_DIR / "data" / "template_bag_count.xlsx"
    
    # Calculate dates for each day of the week
    # Find Monday of this week
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    
    # Date cells (next to day names in row 2)
    DATE_CELLS = {
        "Mon": ("C2", monday.strftime("%m/%d")),
        "Tue": ("G2", (monday + timedelta(days=1)).strftime("%m/%d")),
        "Wed": ("K2", (monday + timedelta(days=2)).strftime("%m/%d")),
        "Thu": ("O2", (monday + timedelta(days=3)).strftime("%m/%d")),
        "Fri": ("S2", (monday + timedelta(days=4)).strftime("%m/%d")),
    }
    
    # Build value updates for each sheet
    def get_values(value_key: str) -> dict:
        """Get cell->value mapping for census or bags."""
        updates = {}
        day_totals = {"Mon": 0, "Tue": 0, "Wed": 0, "Thu": 0, "Fri": 0}
        
        day_map = {
            "D": "Mon", "H": "Tue", "L": "Wed", "P": "Thu", "T": "Fri"
        }
        
        for (fac_key, batch_name), cell_ref in CELL_MAP.items():
            day = day_map.get(cell_ref[0], "")
            fac_counts = counts.get(day, {}).get(fac_key, {})
            fac_batches = batches_config.get(fac_key, [])
            
            # Find batch by name
            val = 0
            for batch in fac_batches:
                if batch["name"] == batch_name:
                    batch_id = batch["id"]
                    values = fac_counts.get(batch_id, {})
                    val = values.get(value_key, 0) or 0
                    break
            
            if val > 0:
                updates[cell_ref] = val
                if day:
                    day_totals[day] += val
        
        # Add daily totals
        for day, cell_ref in DAILY_TOTAL_CELLS.items():
            if day_totals[day] > 0:
                updates[cell_ref] = day_totals[day]
        
        # Add dates next to day names
        for day, (cell_ref, date_str) in DATE_CELLS.items():
            updates[cell_ref] = date_str
        
        return updates, sum(day_totals.values())
    
    census_updates, total_census = get_values("census")
    bags_updates, total_bags = get_values("bags")
    
    result_msg = ""
    (APP_DIR / "data").mkdir(parents=True, exist_ok=True)
    
    # Copy template and update values
    shutil.copy(template_path, filepath)
    
    # Update the xlsx file in place
    def update_sheet_values(zf_path, sheet_name, updates):
        """Update cell values in a sheet while preserving formatting."""
        from xml.etree import ElementTree as ET
        
        sheet_file = "xl/worksheets/sheet1.xml" if sheet_name == "census" else "xl/worksheets/sheet2.xml"
        
        # Read existing xlsx
        with ZipFile(zf_path, 'r') as zf:
            sheet_xml = zf.read(sheet_file).decode('utf-8')
            all_files = {name: zf.read(name) for name in zf.namelist()}
        
        # Parse and update
        ns = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        # Register namespace to avoid ns0 prefix
        ET.register_namespace('', 'http://schemas.openxmlformats.org/spreadsheetml/2006/main')
        ET.register_namespace('r', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')
        
        root = ET.fromstring(sheet_xml)
        sheet_data = root.find('.//main:sheetData', ns)
        
        for cell_ref, value in updates.items():
            # Find or create the cell
            col = ''.join(filter(str.isalpha, cell_ref))
            row_num = ''.join(filter(str.isdigit, cell_ref))
            
            row_elem = sheet_data.find(f".//main:row[@r='{row_num}']", ns)
            if row_elem is None:
                continue
            
            cell_elem = row_elem.find(f".//main:c[@r='{cell_ref}']", ns)
            if cell_elem is None:
                # Create new cell element
                cell_elem = ET.SubElement(row_elem, '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c')
                cell_elem.set('r', cell_ref)
            
            # Remove any existing value or type
            cell_elem.attrib.pop('t', None)
            for child in list(cell_elem):
                if child.tag.endswith('}v') or child.tag.endswith('}is'):
                    cell_elem.remove(child)
            
            # Add new value - handle text vs numbers
            if isinstance(value, (int, float)):
                v_elem = ET.SubElement(cell_elem, '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                v_elem.text = str(value)
            else:
                # Text value - use inline string
                cell_elem.set('t', 'inlineStr')
                is_elem = ET.SubElement(cell_elem, '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is')
                t_elem = ET.SubElement(is_elem, '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
                t_elem.text = str(value)
        
        # Write back
        updated_xml = ET.tostring(root, encoding='unicode')
        # Add XML declaration
        updated_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + updated_xml
        
        all_files[sheet_file] = updated_xml.encode('utf-8')
        
        # Rewrite zip
        with ZipFile(zf_path, 'w', ZIP_DEFLATED) as zf:
            for name, content in all_files.items():
                zf.writestr(name, content)
    
    # Update both sheets
    update_sheet_values(filepath, "census", census_updates)
    update_sheet_values(filepath, "bags", bags_updates)
    
    # Email the report
    subject = f"Weekly Bag Count Report - Week {week_num}, {year}"
    body = f"""Weekly Bag Count Report

Week {week_num}, {year}

Summary:
- Total Bags: {total_bags}
- Total Census: {total_census}

The Excel report is attached with two sheets:
- Cycle Census
- Cycle Bag Count

---
Automated report from Ozark LTC Rx Cycle Tracker
"""
    
    try:
        cmd = [
            "gog", "gmail", "send",
            "--to", email_to,
            "--subject", subject,
            "--body", body,
            "--attach", str(filepath)
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        result_msg = f"Exported to {filename} and emailed to {email_to}."
    except subprocess.TimeoutExpired:
        result_msg = f"Exported to {filename} but email timed out."
    except subprocess.CalledProcessError as e:
        result_msg = f"Exported to {filename} but email failed: {e}"
    except Exception as e:
        result_msg = f"Exported to {filename} but email failed: {e}"
    
    # Reset state for new week if requested
    if reset:
        new_state = {
            "batches": batches_config,
            "counts": {},
            "unlocked_days": [],
            "completed_days": [],
        }
        save_bag_count_state(new_state)
        result_msg += " Reset for new week."
    
    return result_msg
