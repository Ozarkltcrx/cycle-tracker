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
    """Export current week's bag counts to Excel (matching template format) and email it.
    
    Format matches Cycle Bag Count Template with:
    - Two sheets: "Cycle Census" and "Cycle Bag Count"
    - Days as columns: Mon(B-D), Tue(F-H), Wed(J-L), Thu(N-P), Fri(R-T)
    - Each day has: Facility | Batch | Value
    
    Args:
        email_to: Email address to send the report to.
        reset: If True, reset counts after export (for Sunday cron). If False, keep data.
    
    Returns: Status message.
    """
    import subprocess
    from datetime import datetime
    from zipfile import ZIP_DEFLATED, ZipFile
    import urllib.request
    
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
    
    # Facility order by day with display names (matching template)
    FACILITY_CONFIG = {
        "Mon": [
            ("Mother of Good Counsel", "MOGC", ["1st Floor", "2nd Floor", "3rd Floor"]),
            ("McClay", "McClay", ["100 Hall", "L Hall"]),
            ("Belleview", "Belleview", ["Hall A", "Hall B", "Unit"]),
            ("Hillside", "Hillside", ["Hill 1", "Hill 2"]),
            ("Superior Manor", "Superior Manor", ["Hall 100", "Hall 200", "Hall 300", "Hall 400"]),
            ("Walnut Street", "Walnut", [""]),
            ("Colonial Doniphan", "Colonial Doniphan", ["Upstairs", "Down"]),
            ("New Hope", "New Hope", ["ALF", "ILF"]),
        ],
        "Tue": [
            ("Westwood Hills", "Westwood", ["A Wing", "B Wing", "C Wing"]),
            ("Glenfield", "Glenfield", ["100"]),
            ("Granite House", "Granite House", [""]),
            ("Creve Coeur", "Creve Coeur", ["1", "2"]),
        ],
        "Wed": [
            ("Licking RCF", "Licking RCF", ["1"]),
            ("Salem Care Center", "Salem", ["1", "2", "3", "RCF"]),
            ("Salem Residential Care", "Salem", ["RCF"]),
            ("Baisch SNF", "Baisch SNF", ["1", "2"]),
            ("Baisch RCF", "Baisch RCF", ["RCF"]),
            ("Pillars", "Pillars", ["1", "2"]),
            ("Legacy", "Legacy", [""]),
            ("John Knox ALF", "John Knox", ["ALF"]),
            ("John Knox ILF", "John Knox", ["ILF 1", "ILF 2"]),
            ("John Knox MM", "John Knox", ["MM"]),
            ("John Knox CSL", "John Knox", ["CSL"]),
        ],
        "Thu": [
            ("Delta South", "Delta", ["1", "2"]),
            ("Seville", "Seville", [""]),
            ("St Johns", "St Johns", ["1", "2"]),
            ("UCity", "U-City", ["1", "2", "3", "4", "5"]),
            ("Bentley's", "Bentley's", ["1"]),
        ],
        "Fri": [
            ("Colonial Bismark", "Colonial", ["1"]),
            ("Bertrand", "Bertrand", ["1", "2"]),
            ("Oakdale SNF", "Oakdale SNF", ["1", "2"]),
            ("Oakdale ALF", "Oakdale ALF", ["1", "2", "3"]),
            ("Oakdale RCF", "Oakdale RCF", ["1", "2"]),
        ],
    }
    
    # Column mappings: Mon=B-D, Tue=F-H, Wed=J-L, Thu=N-P, Fri=R-T
    DAY_COLS = {
        "Mon": ("B", "C", "D"),
        "Tue": ("F", "G", "H"),
        "Wed": ("J", "K", "L"),
        "Thu": ("N", "O", "P"),
        "Fri": ("R", "S", "T"),
    }
    WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    DAY_NAMES = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday"}
    
    # Get week info
    today = datetime.now()
    week_num = today.isocalendar()[1]
    year = today.year
    filename = f"Cycle_Bag_Count_{year}_W{week_num:02d}.xlsx"
    filepath = APP_DIR / "data" / filename
    
    def xlsx_escape(value):
        if value is None:
            return ""
        s = str(value)
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    def build_sheet_data(value_key: str) -> tuple:
        """Build sheet data for either 'census' or 'bags'."""
        cells = {}  # {(row, col): value}
        total = 0
        
        # Row 2: Day headers
        for day, (col_fac, col_batch, col_val) in DAY_COLS.items():
            cells[(2, col_fac)] = DAY_NAMES[day]
        
        # Row 3: Column headers
        header = "Census" if value_key == "census" else "Bags"
        for day, (col_fac, col_batch, col_val) in DAY_COLS.items():
            cells[(3, col_fac)] = "Facility"
            cells[(3, col_batch)] = "Batch"
            cells[(3, col_val)] = header
        
        # Track max row per day for totals
        day_max_row = {day: 3 for day in WEEKDAYS}
        day_totals = {day: 0 for day in WEEKDAYS}
        
        # Data rows starting at row 4
        for day in WEEKDAYS:
            col_fac, col_batch, col_val = DAY_COLS[day]
            day_counts = counts.get(day, {})
            row = 4
            
            for fac_key, fac_display, expected_batches in FACILITY_CONFIG.get(day, []):
                fac_counts = day_counts.get(fac_key, {})
                fac_batches = batches_config.get(fac_key, [])
                
                if not fac_batches:
                    continue
                
                first_batch = True
                for batch in fac_batches:
                    batch_id = batch["id"]
                    batch_name = batch["name"]
                    values = fac_counts.get(batch_id, {})
                    val = values.get(value_key, 0) or 0
                    
                    if first_batch:
                        cells[(row, col_fac)] = fac_display
                        first_batch = False
                    
                    cells[(row, col_batch)] = batch_name
                    if val > 0:
                        cells[(row, col_val)] = val
                        day_totals[day] += val
                        total += val
                    
                    row += 1
                
                # Add facility subtotal row
                day_max_row[day] = max(day_max_row[day], row)
                row += 1  # Empty row after facility
        
        # Add daily totals at bottom of each day
        total_row = max(day_max_row.values()) + 2
        for day, (col_fac, col_batch, col_val) in DAY_COLS.items():
            cells[(total_row, col_fac)] = "Daily Total " + ("Census" if value_key == "census" else "Bags")
            cells[(total_row, col_val)] = day_totals[day] if day_totals[day] > 0 else 0
        
        return cells, total
    
    def cells_to_sheet_xml(cells: dict) -> str:
        """Convert cells dict to sheet XML."""
        rows_data = {}
        for (row, col), value in cells.items():
            if row not in rows_data:
                rows_data[row] = []
            rows_data[row].append((col, value))
        
        sheet_rows = []
        for row_num in sorted(rows_data.keys()):
            cell_strs = []
            for col, value in sorted(rows_data[row_num], key=lambda x: x[0]):
                cell_ref = f"{col}{row_num}"
                if isinstance(value, (int, float)):
                    cell_strs.append(f'<c r="{cell_ref}"><v>{value}</v></c>')
                else:
                    cell_strs.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{xlsx_escape(value)}</t></is></c>')
            sheet_rows.append(f'<row r="{row_num}">{"".join(cell_strs)}</row>')
        
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{"".join(sheet_rows)}</sheetData>
</worksheet>'''
    
    # Build both sheets
    census_cells, total_census = build_sheet_data("census")
    bags_cells, total_bags = build_sheet_data("bags")
    
    census_xml = cells_to_sheet_xml(census_cells)
    bags_xml = cells_to_sheet_xml(bags_cells)
    
    result_msg = ""
    
    (APP_DIR / "data").mkdir(parents=True, exist_ok=True)
    
    # Build xlsx with two sheets
    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="Cycle Census" sheetId="1" r:id="rId1"/>
<sheet name="Cycle Bag Count" sheetId="2" r:id="rId2"/>
</sheets>
</workbook>'''
    
    rels_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>'''
    
    content_types_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    
    root_rels_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    
    with ZipFile(filepath, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", census_xml)
        zf.writestr("xl/worksheets/sheet2.xml", bags_xml)
    
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
