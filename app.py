#!/usr/bin/env python3
"""
Ozark LTC Rx Pharmacy Operations Command Center
Run: streamlit run app.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st
import yaml
import streamlit_authenticator as stauth
from streamlit_autorefresh import st_autorefresh

import supabase_client as supa

try:
    from openpyxl import load_workbook
    from openpyxl import Workbook as _OpenpyxlWorkbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

CYCLE_STAGE_ORDER = [
    "Pulling meds",
    "Exported",
    "Traying",
    "Running in machine",
    "Through machine",
    "Through perl",
    "Bag check completed",
    "Toted",
    "Facility finished",
]

# $$ Tracking stages (subset for cycle fill billing)
CYCLE_DOLLAR_STAGE_ORDER = [
    "Exported",
    "Through machine",
    "Through Perl",
    "Back Checked",
    "Toted",
    "Facility Complete",
]

CYCLE_TEAM_SCHEDULE = {
    "Mon": ["Sunrise Manor", "Oakwood Care", "Pine Valley", "Shady Pines"],
    "Tue": ["Willow Creek", "Maple Ridge", "Cedar Heights", "Harmony House"],
    "Wed": ["Golden Meadows", "River Bend Care", "Silver Oaks", "Evergreen Terrace"],
    "Thu": ["Liberty Place", "Brookside Manor", "Meadow View", "Fox Hollow"],
    "Fri": ["Pleasant Hill", "Autumn Lake", "Grandview Care", "Heritage Pointe"],
}

CYCLE_STAGE_LABELS = {stage: f"{index + 1}. {stage}" for index, stage in enumerate(CYCLE_STAGE_ORDER)}
CYCLE_DOLLAR_STAGE_LABELS = {stage: f"{index + 1}. {stage}" for index, stage in enumerate(CYCLE_DOLLAR_STAGE_ORDER)}

# Frequency options for facilities
FREQUENCY_OPTIONS = ["Weekly", "Every 2 weeks", "Every 4 weeks"]

# Display string for $$ that won't be interpreted as LaTeX (zero-width space between)
DOLLAR_DISPLAY = "$\u200B$"

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DATA_FILE = DATA_DIR / "mo_ltc_demo.json"
CYCLE_LOG_FILE = DATA_DIR / "cycle_log.xlsx"

DAY_ABBR_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri"]
FACILITIES_FILE = DATA_DIR / "facilities.json"
SHARED_STATE_FILE = DATA_DIR / "shared_tracking_state.json"


def load_shared_state() -> dict:
    """Load shared tracking state (Supabase or local JSON fallback)."""
    return supa.load_tracking_state()


def save_shared_state(state: dict) -> None:
    """Save shared tracking state (Supabase or local JSON fallback)."""
    supa.save_tracking_state(state)


def sync_session_with_shared():
    """Sync session state with shared state file."""
    shared = load_shared_state()
    for key in ["cycle_team_tracking", "dollar_tracking", "unlocked_days", "dollar_unlocked_days"]:
        if key in shared and shared[key]:
            st.session_state[key] = shared[key]


def save_session_to_shared():
    """Save relevant session state to shared file."""
    state = {
        "cycle_team_tracking": st.session_state.get("cycle_team_tracking", {}),
        "dollar_tracking": st.session_state.get("dollar_tracking", {}),
        "unlocked_days": st.session_state.get("unlocked_days", []),
        "dollar_unlocked_days": st.session_state.get("dollar_unlocked_days", []),
    }
    save_shared_state(state)


def get_week_dates() -> dict[str, str]:
    """Return dict mapping day abbrev to formatted date string for current week."""
    today = datetime.now()
    # Monday = 0, so we go back to Monday of this week
    monday = today - timedelta(days=today.weekday())
    dates = {}
    for i, day in enumerate(DAY_ABBR_ORDER):
        day_date = monday + timedelta(days=i)
        dates[day] = day_date.strftime("%b %d")
    return dates


def load_facilities_config() -> dict[str, list[dict]]:
    """Load facility schedule from Supabase / JSON, or use default.

    Returns dict mapping day -> list of facility dicts with keys:
    - name: str
    - frequency: str ("Weekly", "Every 2 weeks", "Every 4 weeks")
    """
    # Try Supabase first
    db_data = supa.load_facilities_config_db()
    raw = db_data if db_data else (json.loads(FACILITIES_FILE.read_text()) if FACILITIES_FILE.exists() else None)

    if raw:
        migrated = {}
        for day, facilities in raw.items():
            migrated[day] = []
            for fac in facilities:
                if isinstance(fac, str):
                    migrated[day].append({"name": fac, "frequency": "Weekly"})
                else:
                    if "frequency" not in fac:
                        fac["frequency"] = "Weekly"
                    migrated[day].append(fac)
        return migrated
    # Default schedule
    return {
        day: [{"name": fac, "frequency": "Weekly"} for fac in facilities]
        for day, facilities in CYCLE_TEAM_SCHEDULE.items()
    }


def get_facility_names(config: dict[str, list[dict]]) -> dict[str, list[str]]:
    """Extract just facility names from config (for backward compat)."""
    return {day: [f["name"] for f in facs] for day, facs in config.items()}


def save_facilities_config(config: dict[str, list[dict]]) -> None:
    """Save facility schedule to Supabase and local JSON."""
    supa.save_facilities_config_db(config)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FACILITIES_FILE.write_text(json.dumps(config, indent=2))


def get_current_week_number() -> int:
    """Get ISO week number for current date."""
    return datetime.now().isocalendar()[1]


def get_week_start_date(target_date: datetime | None = None) -> datetime:
    """Get the Monday of the week containing target_date (or today)."""
    if target_date is None:
        target_date = datetime.now()
    return target_date - timedelta(days=target_date.weekday())


def facility_active_this_week(frequency: str, start_date: str | None = None) -> bool:
    """Check if facility is active this week based on frequency and start date.
    
    Args:
        frequency: "Weekly", "Every 2 weeks", or "Every 4 weeks"
        start_date: ISO date string (YYYY-MM-DD) of first run, or None for Weekly
    """
    if frequency == "Weekly":
        return True
    
    if not start_date:
        # No start date set, default to active (treat as Weekly)
        return True
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return True
    
    # Get Monday of start week and current week
    start_week_monday = get_week_start_date(start)
    current_week_monday = get_week_start_date()
    
    # Calculate weeks since start
    weeks_diff = (current_week_monday - start_week_monday).days // 7
    
    if frequency == "Every 2 weeks":
        return weeks_diff % 2 == 0
    elif frequency == "Every 4 weeks":
        return weeks_diff % 4 == 0
    
    return True


def get_next_run_date(frequency: str, start_date: str | None, day_abbr: str) -> str:
    """Get the next run date for a facility based on frequency."""
    if frequency == "Weekly":
        # Next occurrence of this day
        today = datetime.now()
        day_idx = DAY_ABBR_ORDER.index(day_abbr)
        days_until = (day_idx - today.weekday()) % 7
        if days_until == 0 and today.hour >= 18:  # Past 6pm, show next week
            days_until = 7
        next_date = today + timedelta(days=days_until)
        return next_date.strftime("%b %d")
    
    if not start_date:
        return "Not set"
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return "Invalid"
    
    today = datetime.now()
    interval = 14 if frequency == "Every 2 weeks" else 28
    
    # Find next occurrence on or after today
    current = start
    while current < today:
        current += timedelta(days=interval)
    
    return current.strftime("%b %d")


def _today_abbr() -> str:
    return datetime.now().strftime("%a")[:3]


def get_visible_days(tracking_state: dict) -> list[str]:
    """Return days to show: today + any prior day with incomplete facilities."""
    today = _today_abbr()
    if today not in DAY_ABBR_ORDER:
        return DAY_ABBR_ORDER  # weekend: show all
    today_idx = DAY_ABBR_ORDER.index(today)
    visible = []
    for i, day in enumerate(DAY_ABBR_ORDER):
        if i == today_idx:
            visible.append(day)
            continue
        if i > today_idx:
            continue  # future days hidden
        # Past day: show only if has incomplete facilities
        day_state = tracking_state.get(day, {})
        for fac in CYCLE_TEAM_SCHEDULE.get(day, []):
            c, t = stage_counts(day_state.get(fac, {}))
            if c < t:
                visible.append(day)
                break
    return visible


def _excel_col_name(index: int) -> str:
    result = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_sheet_xml(rows: list[list[Any]]) -> str:
    dimension = f"A1:E{max(len(rows), 1)}"
    row_xml = []
    for row_idx, row in enumerate(rows, start=1):
        cell_xml = []
        for col_idx, value in enumerate(row, start=1):
            cell_ref = f"{_excel_col_name(col_idx)}{row_idx}"
            cell_xml.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>{_xlsx_escape(value)}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_idx}">{"".join(cell_xml)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '</worksheet>'
    )


def _write_basic_xlsx(path: Path, rows: list[list[Any]]) -> None:
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Cycle Log" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    core = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Cycle Log</dc:title>
  <dc:creator>Command Center</dc:creator>
</cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Command Center</Application>
</Properties>'''

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(rows))


def _read_basic_xlsx_rows(path: Path) -> list[list[str]]:
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml")
    root = ET.fromstring(sheet_xml)
    rows: list[list[str]] = []
    for row in root.findall("main:sheetData/main:row", ns):
        current: list[str] = []
        for cell in row.findall("main:c", ns):
            inline = cell.find("main:is/main:t", ns)
            value = inline.text if inline is not None and inline.text is not None else ""
            current.append(value)
        rows.append(current)
    return rows


def ensure_cycle_log_file() -> None:
    """Create the Excel audit file with headers if it does not already exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    headers = ["Facility", "Stage", "Initials", "Date", "Time"]
    if CYCLE_LOG_FILE.exists():
        return
    if HAS_OPENPYXL:
        wb = _OpenpyxlWorkbook()
        ws = wb.active
        ws.title = "Cycle Log"
        ws.append(headers)
        wb.save(CYCLE_LOG_FILE)
        return
    _write_basic_xlsx(CYCLE_LOG_FILE, [headers])


def log_stage_to_excel(facility: str, stage: str, initials: str) -> None:
    """Append a completion record to audit log (Supabase + Excel)."""
    supa.log_audit_entry(facility, stage, initials)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    row = [facility, stage, initials, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")]
    headers = ["Facility", "Stage", "Initials", "Date", "Time"]
    if HAS_OPENPYXL:
        if CYCLE_LOG_FILE.exists():
            wb = load_workbook(CYCLE_LOG_FILE)
            ws = wb.active
        else:
            wb = _OpenpyxlWorkbook()
            ws = wb.active
            ws.title = "Cycle Log"
            ws.append(headers)
        ws.append(row)
        wb.save(CYCLE_LOG_FILE)
        return

    existing_rows = _read_basic_xlsx_rows(CYCLE_LOG_FILE) if CYCLE_LOG_FILE.exists() else []
    if not existing_rows:
        existing_rows = [headers]
    existing_rows.append(row)
    _write_basic_xlsx(CYCLE_LOG_FILE, existing_rows)

CUSTOM_CSS = """
<style>
    .stApp {
        background: linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%);
    }
    section[data-testid="stSidebar"] {
        background: #0f172a !important;
    }
    section[data-testid="stSidebar"] * {
        color: white !important;
    }
    /* Sidebar nav button styling */
    section[data-testid="stSidebar"] button[kind="secondary"] {
        background: #2563eb !important;
        color: white !important;
        border: none !important;
        margin-bottom: 4px !important;
    }
    section[data-testid="stSidebar"] button[kind="secondary"]:hover {
        background: #1d4ed8 !important;
        color: white !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"] {
        background: #1e40af !important;
        color: white !important;
        border-left: 4px solid white !important;
        margin-bottom: 4px !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"]:hover {
        background: #1e3a8a !important;
    }

    .hero-card, .block-card {
        background: white;
        border: 1px solid #dbe4f0;
        border-radius: 18px;
        padding: 18px 20px;
        box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
        margin-bottom: 12px;
    }
    .hero-title {
        font-size: 1.6rem;
        font-weight: 800;
        color: #0f172a;
    }
    .muted {
        color: #64748b;
        font-size: 0.95rem;
    }
    .pill {
        display: inline-block;
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 700;
        margin-right: 8px;
        margin-bottom: 8px;
        background: #e0e7ff;
        color: #3730a3;
    }
</style>
"""

RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2}
STATUS_COLORS = {
    "High": "#dc2626",
    "Medium": "#d97706",
    "Low": "#059669",
    "At Risk": "#dc2626",
    "Watch": "#d97706",
    "On Track": "#059669",
    "Delayed": "#dc2626",
    "In progress": "#2563eb",
    "Complete": "#059669",
}


@st.cache_data
def load_demo_data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def risk_badge(label: str) -> str:
    color = STATUS_COLORS.get(label, "#475569")
    return (
        f"<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
        f"background:{color};color:white;font-size:12px;font-weight:700;'>{label}</span>"
    )


def metric_block(label: str, value: Any, help_text: str) -> None:
    st.metric(label, value, help=help_text)


def build_cycle_team_state() -> dict[str, dict[str, dict[str, str]]]:
    return {
        day: {
            facility: {stage: "" for stage in CYCLE_STAGE_ORDER}
            for facility in facilities
        }
        for day, facilities in CYCLE_TEAM_SCHEDULE.items()
    }


def stage_counts(stage_map: dict[str, str], stage_order: list[str] = None) -> tuple[int, int]:
    if stage_order is None:
        stage_order = CYCLE_STAGE_ORDER
    completed = sum(1 for stage in stage_order if stage_map.get(stage, ""))
    return completed, len(stage_order)


def cycle_status_label(stage_map: dict[str, str], stage_order: list[str] = None) -> str:
    if stage_order is None:
        stage_order = CYCLE_STAGE_ORDER
    completed, total = stage_counts(stage_map, stage_order)
    if completed == 0:
        return "Not Started"
    if completed >= total:
        return "Completed"

    for stage in reversed(stage_order):
        if stage_map.get(stage, ""):
            return stage

    return "Not Started"


def cycle_status_color(status: str) -> str:
    return {
        "Not Started": "#94a3b8",
        "Completed": "#059669",
    }.get(status, "#2563eb")


def render_cycle_facility(day: str, facility: str, stage_map: dict[str, str]) -> None:
    completed, total = stage_counts(stage_map)
    progress_pct = completed / total
    status = cycle_status_label(stage_map)
    accent = cycle_status_color(status)

    # Build expander label with progress info
    pct_display = int(progress_pct * 100)
    expander_label = f"**{facility}** — {status} ({pct_display}%)"
    
    with st.expander(expander_label, expanded=False):
        initials_summary = ", ".join(
            f"{stage}: {initials}"
            for stage, initials in stage_map.items()
            if initials
        )
        
        progress_col, summary_col = st.columns([1.25, 1])
        with progress_col:
            st.progress(progress_pct, text=f"{pct_display}% complete")
        with summary_col:
            if status == "Completed":
                st.success("Completed and ready to roll.")
            elif status == "Not Started":
                st.caption("No stages marked yet.")
            else:
                st.info(f"Latest: {status}")
            if initials_summary:
                st.caption(f"By: {initials_summary}")

        stage_cols = st.columns(3)
        for idx, stage in enumerate(CYCLE_STAGE_ORDER):
            initials_key = f"cycle_initials::{day}::{facility}::{stage}"
            form_key = f"cycle_form::{day}::{facility}::{stage}"
            completed_initials = stage_map.get(stage, "")
            with stage_cols[idx % 3]:
                if completed_initials:
                    st.success(f"{CYCLE_STAGE_LABELS[stage]}\n\n✓ {completed_initials}")
                else:
                    with st.form(form_key, clear_on_submit=True):
                        initials_value = st.text_input(
                            CYCLE_STAGE_LABELS[stage],
                            max_chars=4,
                            key=initials_key,
                            placeholder="AB",
                        )
                        submitted = st.form_submit_button("Mark complete", use_container_width=True)
                        if submitted:
                            cleaned = initials_value.strip().upper()
                            if cleaned:
                                stage_map[stage] = cleaned
                                st.session_state.cycle_team_tracking[day][facility][stage] = cleaned
                                log_stage_to_excel(facility, stage, cleaned)
                                save_session_to_shared()  # Sync to shared state
                                st.rerun()
                            else:
                                st.warning("Enter initials to complete the stage.")


def render_dollar_facility(day: str, facility: str, stage_map: dict[str, str]) -> None:
    """Render a facility card for $$ tracking with different stages."""
    # Check for "No meds" bypass
    no_meds = stage_map.get("_no_meds", False)
    
    if no_meds:
        # Show as bypassed
        expander_label = f"**{facility} {DOLLAR_DISPLAY}** — No Meds ⏭️"
        with st.expander(expander_label, expanded=False):
            st.info("🚫 No meds to run — bypassed")
            if st.button(f"↩️ Undo bypass", key=f"undo_nomeds_{day}_{facility}"):
                del st.session_state.dollar_tracking[day][facility]["_no_meds"]
                save_session_to_shared()
                st.rerun()
        return
    
    completed, total = stage_counts(stage_map, CYCLE_DOLLAR_STAGE_ORDER)
    progress_pct = completed / total if total > 0 else 0
    status = cycle_status_label(stage_map, CYCLE_DOLLAR_STAGE_ORDER)
    
    # Build expander label with $$ suffix
    pct_display = int(progress_pct * 100)
    expander_label = f"**{facility} {DOLLAR_DISPLAY}** — {status} ({pct_display}%)"
    
    with st.expander(expander_label, expanded=False):
        # No meds bypass button at top
        bypass_col, spacer_col = st.columns([1, 2])
        with bypass_col:
            if st.button("🚫 No meds to run", key=f"nomeds_{day}_{facility}", use_container_width=True):
                st.session_state.dollar_tracking[day][facility]["_no_meds"] = True
                log_stage_to_excel(f"{facility} $$", "NO MEDS", "BYPASS")
                save_session_to_shared()
                st.rerun()
        
        initials_summary = ", ".join(
            f"{stage}: {initials}"
            for stage, initials in stage_map.items()
            if initials and not stage.startswith("_")
        )
        
        progress_col, summary_col = st.columns([1.25, 1])
        with progress_col:
            st.progress(progress_pct, text=f"{pct_display}% complete")
        with summary_col:
            if status == "Completed":
                st.success(f"{DOLLAR_DISPLAY} Complete!")
            elif status == "Not Started":
                st.caption("No stages marked yet.")
            else:
                st.info(f"Latest: {status}")
            if initials_summary:
                st.caption(f"By: {initials_summary}")

        stage_cols = st.columns(3)
        for idx, stage in enumerate(CYCLE_DOLLAR_STAGE_ORDER):
            initials_key = f"dollar_initials::{day}::{facility}::{stage}"
            form_key = f"dollar_form::{day}::{facility}::{stage}"
            completed_initials = stage_map.get(stage, "")
            with stage_cols[idx % 3]:
                if completed_initials:
                    st.success(f"{CYCLE_DOLLAR_STAGE_LABELS[stage]}\n\n✓ {completed_initials}")
                else:
                    with st.form(form_key, clear_on_submit=True):
                        initials_value = st.text_input(
                            CYCLE_DOLLAR_STAGE_LABELS[stage],
                            max_chars=4,
                            key=initials_key,
                            placeholder="AB",
                        )
                        submitted = st.form_submit_button("Mark complete", use_container_width=True)
                        if submitted:
                            cleaned = initials_value.strip().upper()
                            if cleaned:
                                stage_map[stage] = cleaned
                                st.session_state.dollar_tracking[day][facility][stage] = cleaned
                                log_stage_to_excel(f"{facility} $$", stage, cleaned)
                                save_session_to_shared()
                                st.rerun()
                            else:
                                st.warning("Enter initials to complete the stage.")


ensure_cycle_log_file()

st.set_page_config(page_title="Ozark LTC Rx Ops Center", page_icon="💊", layout="wide")

# --- Authentication (with Supabase persistence for 30-day sessions) ---
CONFIG_PATH = APP_DIR / "config.yaml"


def load_merged_config() -> dict:
    """Load config from Supabase (if available), merged with local config.yaml.
    
    Supabase config takes precedence for credentials/permissions, ensuring
    user accounts persist across Streamlit Cloud restarts.
    """
    # Start with local config.yaml as base
    local_config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            local_config = yaml.safe_load(f) or {}
    
    # Try to load from Supabase
    db_config = supa.load_auth_config_db()
    
    if db_config:
        # Supabase config takes precedence for credentials and permissions
        merged = local_config.copy()
        if "credentials" in db_config:
            merged["credentials"] = db_config["credentials"]
        if "permissions" in db_config:
            merged["permissions"] = db_config["permissions"]
        return merged
    
    return local_config


def save_config(config: dict) -> None:
    """Save config to both Supabase (for persistence) and local file.
    
    This ensures user accounts survive Streamlit Cloud restarts.
    """
    # Save to Supabase first (this persists across restarts)
    supa.save_auth_config_db(config)
    
    # Also save locally (for immediate use and backup)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


# Load merged config (Supabase + local)
config = load_merged_config()

if config and config.get('credentials') and config.get('cookie'):
    authenticator = stauth.Authenticate(
        config['credentials'],
        config['cookie']['name'],
        config['cookie']['key'],
        config['cookie']['expiry_days'],  # 30 days as configured
    )
    
    authenticator.login(location='main')
    
    if st.session_state.get("authentication_status") is None:
        st.warning("Please enter your username and password")
        st.stop()
    elif st.session_state.get("authentication_status") is False:
        st.error("Username/password is incorrect")
        st.stop()
    
    # User is authenticated - show logout in sidebar later
else:
    st.error("No authentication config found. Please check config.yaml and Supabase.")
    st.stop()
# --- End Authentication ---

# --- Auto-refresh for real-time sync (every 30 seconds) ---
st_autorefresh(interval=30000, limit=None, key="auto_refresh")

# --- Sync with shared state ---
sync_session_with_shared()

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

data = load_demo_data()
facilities = pd.DataFrame(data["facilities"])
daily_ops = pd.DataFrame(data["daily_ops"])
adt = pd.DataFrame(data["adt_events"])
cycle_fill = pd.DataFrame(data["cycle_fill"])
delivery = pd.DataFrame(data["delivery_runs"])
emergency_kits = pd.DataFrame(data["emergency_kits"])

facility_names = ["All facilities", *facilities["facility"].tolist()]
selected_facility = "All facilities"

# --- Permission System ---
ALL_PAGES = [
    "Dashboard",
    "Cycle Team",
    "Facility Management",
    "Data Explorer",
    "User Management",
]

def get_user_permissions():
    """Get list of pages the current user can access."""
    if not CONFIG_PATH.exists():
        return ALL_PAGES  # No auth = full access
    
    username = st.session_state.get("username")
    if not username:
        return []
    
    user_data = config.get("credentials", {}).get("usernames", {}).get(username, {})
    role = user_data.get("role", "user")
    
    permissions = config.get("permissions", {})
    
    # Check user-specific permissions first
    user_perms = permissions.get("users", {}).get(username)
    if user_perms:
        return user_perms
    
    # Fall back to role permissions
    role_perms = permissions.get("roles", {}).get(role, [])
    if "all" in role_perms:
        return ALL_PAGES
    return role_perms

def is_admin_user():
    if not CONFIG_PATH.exists():
        return True
    username = st.session_state.get("username")
    if username and username in config.get("credentials", {}).get("usernames", {}):
        return config["credentials"]["usernames"][username].get("role") == "admin"
    return False

# Get pages this user can see
allowed_pages = get_user_permissions()

with st.sidebar:
    st.title("💊 Ozark LTC Rx")
    
    # Show logged-in user and logout button
    if CONFIG_PATH.exists() and st.session_state.get("authentication_status"):
        st.caption(f"👤 **{st.session_state.get('name', 'User')}**")
        # Red logout button using custom HTML
        st.markdown("""
            <style>
            div[data-testid="stSidebar"] > div > div > div > div:nth-child(3) button {
                background: #dc2626 !important;
                color: white !important;
            }
            div[data-testid="stSidebar"] > div > div > div > div:nth-child(3) button:hover {
                background: #b91c1c !important;
            }
            </style>
        """, unsafe_allow_html=True)
        authenticator.logout("🚪 Logout", "sidebar")
        st.divider()
    
    # Navigation
    st.subheader("Navigation")
    
    # Only show pages user has access to
    if "current_page" not in st.session_state:
        st.session_state.current_page = allowed_pages[0] if allowed_pages else "Dashboard"
    
    # Ensure current page is allowed
    if st.session_state.current_page not in allowed_pages and allowed_pages:
        st.session_state.current_page = allowed_pages[0]
    
    for page in ALL_PAGES:
        if page in allowed_pages:
            if st.button(
                f"{'→ ' if st.session_state.current_page == page else '   '}{page}",
                key=f"nav_{page}",
                use_container_width=True,
                type="primary" if st.session_state.current_page == page else "secondary"
            ):
                st.session_state.current_page = page
                st.rerun()
    
    st.divider()
    st.caption("Concrete pharmacy operations prototype")
    selected_facility = st.selectbox("Facility filter", facility_names)
    st.divider()
    st.caption(f"Facilities: {len(facilities)} · ADT: {len(adt)}")
    st.caption(f"Routes: {len(delivery)} · E-Kits: {len(emergency_kits)}")

if selected_facility != "All facilities":
    facilities_view = facilities[facilities["facility"] == selected_facility].copy()
    adt_view = adt[adt["facility"] == selected_facility].copy()
    cycle_view = cycle_fill[cycle_fill["facility"] == selected_facility].copy()
    ekit_view = emergency_kits[emergency_kits["facility"] == selected_facility].copy()
else:
    facilities_view = facilities.copy()
    adt_view = adt.copy()
    cycle_view = cycle_fill.copy()
    ekit_view = emergency_kits.copy()

high_risk_count = int((facilities_view["risk_level"] == "High").sum())
pending_orders = int(facilities_view["pending_orders"].sum())
overdue_first_doses = int(facilities_view["overdue_first_doses"].sum())
adt_admissions = int((adt_view["event_type"] == "Admission").sum())
packed_total = int(cycle_view["packed"].sum()) if not cycle_view.empty else 0
residents_due_total = int(cycle_view["residents_due"].sum()) if not cycle_view.empty else 0
fill_completion = round((packed_total / residents_due_total) * 100) if residents_due_total else 0

# Get current page
current_page = st.session_state.get("current_page", "Dashboard")

# --- PAGE: Dashboard ---
if current_page == "Dashboard":
    st.title("Dashboard")
    st.caption(f"Overview as of {datetime.now().strftime('%A %Y-%m-%d %H:%M')}")

    # Load facility config and tracking state
    _dash_facilities_raw = load_facilities_config()
    _dash_facilities: dict[str, list[str]] = {}
    for _d, _fl in _dash_facilities_raw.items():
        _dash_facilities[_d] = [
            f["name"] for f in _fl
            if facility_active_this_week(f.get("frequency", "Weekly"), f.get("start_date"))
        ]

    _dash_tracking = st.session_state.get("cycle_team_tracking", {})
    _dash_dollar = st.session_state.get("dollar_tracking", {})

    today_key = _today_abbr()
    today_idx = DAY_ABBR_ORDER.index(today_key) if today_key in DAY_ABBR_ORDER else -1
    now_hour = datetime.now().hour + datetime.now().minute / 60.0

    # Collect rows: today's facilities + previous-day incomplete
    dash_rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # Previous days incomplete
    if today_key in DAY_ABBR_ORDER:
        for prior_day in DAY_ABBR_ORDER[:today_idx]:
            for fac in _dash_facilities.get(prior_day, []):
                sm = _dash_tracking.get(prior_day, {}).get(fac, {})
                c, t = stage_counts(sm)
                if c < t:
                    pct = int(c / t * 100) if t else 0
                    # Dollar status
                    dm = _dash_dollar.get(prior_day, {}).get(fac, {})
                    if dm.get("_no_meds"):
                        dollar_st = "No Meds"
                    else:
                        dc, dt_ = stage_counts(dm, CYCLE_DOLLAR_STAGE_ORDER)
                        dollar_st = "Completed" if dc >= dt_ else ("In Progress" if dc > 0 else "Not Started")
                    dash_rows.append({
                        "Day": prior_day,
                        "Facility": fac,
                        "Progress": f"{c}/{t} ({pct}%)",
                        "Status": f"Overdue ({prior_day})",
                        "High Dollar": dollar_st,
                        "_overdue": True,
                        "_pct": pct,
                    })
                    seen.add((prior_day, fac))

    # Today's facilities
    for fac in _dash_facilities.get(today_key, []):
        if (today_key, fac) in seen:
            continue
        sm = _dash_tracking.get(today_key, {}).get(fac, {})
        c, t = stage_counts(sm)
        pct = int(c / t * 100) if t else 0
        status_label = cycle_status_label(sm)

        # Dollar status
        dm = _dash_dollar.get(today_key, {}).get(fac, {})
        if dm.get("_no_meds"):
            dollar_st = "No Meds"
        else:
            dc, dt_ = stage_counts(dm, CYCLE_DOLLAR_STAGE_ORDER)
            dollar_st = "Completed" if dc >= dt_ else ("In Progress" if dc > 0 else "Not Started")

        # Smart status highlighting via 4-week average
        avg_hour = supa.get_average_completion_hour(fac, "Facility finished", weeks=4)
        highlight = ""
        if avg_hour is not None:
            diff_minutes = (now_hour - avg_hour) * 60
            if status_label == "Completed":
                # Completed: check if completed early
                completion_times = supa.get_facility_completion_times(fac, "Facility finished", weeks=0)
                # For today, just compare now vs average — if we're 30+ min before avg, green
                if diff_minutes <= -30:
                    highlight = "early"
            else:
                # Not complete yet
                if diff_minutes >= 30:
                    highlight = "late"

        dash_rows.append({
            "Day": today_key,
            "Facility": fac,
            "Progress": f"{c}/{t} ({pct}%)",
            "Status": status_label,
            "High Dollar": dollar_st,
            "_overdue": False,
            "_pct": pct,
            "_highlight": highlight,
        })

    if dash_rows:
        # Summary metrics
        total = len(dash_rows)
        completed = sum(1 for r in dash_rows if r["Status"] == "Completed")
        overdue = sum(1 for r in dash_rows if r.get("_overdue"))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Facilities", total)
        m2.metric("Completed", completed)
        m3.metric("In Progress", total - completed - overdue)
        m4.metric("Overdue", overdue)

        # Render the grid with colored status cells
        for row in dash_rows:
            highlight = row.get("_highlight", "")
            is_overdue = row.get("_overdue", False)

            if is_overdue:
                bg = "#fef2f2"
                border_color = "#fca5a5"
            elif highlight == "early":
                bg = "#f0fdf4"
                border_color = "#86efac"
            elif highlight == "late":
                bg = "#fef2f2"
                border_color = "#fca5a5"
            else:
                bg = "white"
                border_color = "#dbe4f0"

            status_text = row["Status"]
            if highlight == "early":
                status_text += " (ahead of avg)"
            elif highlight == "late":
                status_text += " (behind avg)"

            cols = st.columns([2, 1.5, 1.5, 1.2])
            with cols[0]:
                st.markdown(
                    f"<div style='background:{bg};border:1px solid {border_color};border-radius:8px;"
                    f"padding:8px 12px;font-weight:600;'>{row['Facility']}"
                    f"<span style='color:#64748b;font-weight:400;font-size:0.85em;'> ({row['Day']})</span></div>",
                    unsafe_allow_html=True,
                )
            with cols[1]:
                st.markdown(
                    f"<div style='background:{bg};border:1px solid {border_color};border-radius:8px;"
                    f"padding:8px 12px;'>{row['Progress']}</div>",
                    unsafe_allow_html=True,
                )
            with cols[2]:
                st.markdown(
                    f"<div style='background:{bg};border:1px solid {border_color};border-radius:8px;"
                    f"padding:8px 12px;'>{status_text}</div>",
                    unsafe_allow_html=True,
                )
            with cols[3]:
                st.markdown(
                    f"<div style='background:{bg};border:1px solid {border_color};border-radius:8px;"
                    f"padding:8px 12px;'>{row['High Dollar']}</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.info("No facilities scheduled for today.")

# --- PAGE: Cycle Team (combined with internal tabs) ---
if current_page == "Cycle Team":
    st.title("Cycle Team")
    
    # Load facility config once for all tabs
    _cycle_facilities_raw = load_facilities_config()
    _cycle_facilities = {}
    for _day, _fac_list in _cycle_facilities_raw.items():
        _cycle_facilities[_day] = [
            f["name"] for f in _fac_list 
            if facility_active_this_week(f.get("frequency", "Weekly"), f.get("start_date"))
        ]
    
    # Initialize tracking states
    if "cycle_team_tracking" not in st.session_state:
        st.session_state.cycle_team_tracking = {
            day: {fac: {stage: "" for stage in CYCLE_STAGE_ORDER} for fac in facs}
            for day, facs in _cycle_facilities.items()
        }
    if "dollar_tracking" not in st.session_state:
        st.session_state.dollar_tracking = {
            day: {fac: {stage: "" for stage in CYCLE_DOLLAR_STAGE_ORDER} for fac in facs}
            for day, facs in _cycle_facilities.items()
        }
    if "unlocked_days" not in st.session_state:
        st.session_state.unlocked_days = []
    if "dollar_unlocked_days" not in st.session_state:
        st.session_state.dollar_unlocked_days = []
    
    # Create internal tabs - Dashboard first
    cycle_tab1, cycle_tab2, cycle_tab3, cycle_tab4 = st.tabs([
        "📊 Cycle Team Dashboard",
        "📋 Cycle Team Tracking",
        "💰 Cycle High Dollar Tracking",
        "📦 Cycle Bag Count"
    ])
    
    # ============ TAB 1: DASHBOARD ============
    with cycle_tab1:
        week_num = get_current_week_number()
        tracking = st.session_state.cycle_team_tracking
        dollar_tracking = st.session_state.dollar_tracking
        
        st.markdown("### Cycle Team Dashboard")
        st.caption(f"Supervisor summary as of {datetime.now().strftime('%A %Y-%m-%d %H:%M')} (Week {week_num})")
        
        today_key = _today_abbr()
        if today_key in _cycle_facilities and _cycle_facilities[today_key]:
            st.markdown(f"#### Today ({today_key})")
            today_rows = []
            for fac in _cycle_facilities[today_key]:
                stage_map = tracking.get(today_key, {}).get(fac, {})
                c, t = stage_counts(stage_map)
                status = cycle_status_label(stage_map)
                pct = int(c / t * 100) if t else 0
                
                dollar_map = dollar_tracking.get(today_key, {}).get(fac, {})
                if dollar_map.get("_no_meds"):
                    dollar_status = "No Meds"
                else:
                    dc, dt = stage_counts(dollar_map, CYCLE_DOLLAR_STAGE_ORDER)
                    if dc == 0:
                        dollar_status = "Not Started"
                    elif dc >= dt:
                        dollar_status = "Completed"
                    else:
                        dollar_status = "In Progress"
                
                today_rows.append({
                    "Facility": fac,
                    "Progress": f"{c}/{t} ({pct}%)",
                    "Status": status,
                    "High Dollar": dollar_status
                })
            
            today_df = pd.DataFrame(today_rows)
            done_count = sum(1 for r in today_rows if r["Status"] == "Completed")
            dollar_done = sum(1 for r in today_rows if r["High Dollar"] in ["Completed", "No Meds"])
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Facilities", len(today_rows))
            m2.metric("Cycle Complete", done_count)
            m3.metric("High Dollar Complete", dollar_done)
            m4.metric("Remaining", len(today_rows) - done_count)
            st.dataframe(today_df, use_container_width=True, hide_index=True)
        else:
            st.info("No cycle schedule for today.")
        
        # Overdue section
        overdue_rows = []
        if today_key in DAY_ABBR_ORDER:
            today_idx = DAY_ABBR_ORDER.index(today_key)
            for day in DAY_ABBR_ORDER[:today_idx]:
                day_state = tracking.get(day, {})
                for fac in _cycle_facilities.get(day, []):
                    c, t = stage_counts(day_state.get(fac, {}))
                    if c < t:
                        overdue_rows.append({"Day": day, "Facility": fac, "Progress": f"{c}/{t}"})
        
        if overdue_rows:
            st.markdown("#### Overdue / Carryover")
            st.warning(f"{len(overdue_rows)} facility(ies) from prior days still incomplete.")
            st.dataframe(pd.DataFrame(overdue_rows), use_container_width=True, hide_index=True)
        else:
            st.success("No overdue facilities from prior days.")
    
    # ============ TAB 2: CYCLE TEAM TRACKING ============
    with cycle_tab2:
        cycle_facilities = _cycle_facilities
        
        for day, facs in cycle_facilities.items():
            if day not in st.session_state.cycle_team_tracking:
                st.session_state.cycle_team_tracking[day] = {}
            for fac in facs:
                if fac not in st.session_state.cycle_team_tracking[day]:
                    st.session_state.cycle_team_tracking[day][fac] = {stage: "" for stage in CYCLE_STAGE_ORDER}
        
        st.markdown("### Cycle Team Tracking")
        today_abbr = _today_abbr()
        week_dates = get_week_dates()
        today_idx = DAY_ABBR_ORDER.index(today_abbr) if today_abbr in DAY_ABBR_ORDER else -1
        
        def get_visible_days_cycle(ts, fc):
            if today_abbr not in DAY_ABBR_ORDER:
                return DAY_ABBR_ORDER
            visible = []
            for i, d in enumerate(DAY_ABBR_ORDER):
                if i == today_idx:
                    visible.append(d)
                elif i < today_idx:
                    for f in fc.get(d, []):
                        c, t = stage_counts(ts.get(d, {}).get(f, {}))
                        if c < t:
                            visible.append(d)
                            break
            return visible
        
        visible_days = get_visible_days_cycle(st.session_state.cycle_team_tracking, cycle_facilities)
        if not visible_days:
            visible_days = [today_abbr] if today_abbr in DAY_ABBR_ORDER else DAY_ABBR_ORDER
        
        for unlocked in st.session_state.unlocked_days:
            if unlocked not in visible_days and unlocked in DAY_ABBR_ORDER:
                visible_days.append(unlocked)
        visible_days = sorted(set(visible_days), key=lambda d: DAY_ABBR_ORDER.index(d) if d in DAY_ABBR_ORDER else 99)
        
        future_days = DAY_ABBR_ORDER[today_idx + 1:] if today_idx >= 0 else []
        next_unlockable = next((fd for fd in future_days if fd not in st.session_state.unlocked_days), None)
        
        st.caption("Click a facility to expand and mark stages complete.")
        btn_cols = st.columns(5)
        col_idx = 0
        for unlocked in st.session_state.unlocked_days:
            if col_idx < 5:
                with btn_cols[col_idx]:
                    if st.button(f"🔒 Lock {unlocked}", key=f"relock_{unlocked}", use_container_width=True):
                        st.session_state.unlocked_days.remove(unlocked)
                        st.session_state.unlocked_days = [d for d in st.session_state.unlocked_days if DAY_ABBR_ORDER.index(d) < DAY_ABBR_ORDER.index(unlocked)]
                        save_session_to_shared()
                        st.rerun()
                col_idx += 1
        if next_unlockable and col_idx < 5:
            with btn_cols[col_idx]:
                if st.button(f"🔓 Unlock {next_unlockable}", key=f"unlock_{next_unlockable}", use_container_width=True):
                    st.session_state.unlocked_days.append(next_unlockable)
                    save_session_to_shared()
                    st.rerun()
        
        day_tabs = st.tabs([f"{day} ({week_dates.get(day, '')})" for day in visible_days])
        for day_tab, day in zip(day_tabs, visible_days):
            with day_tab:
                day_idx_loop = DAY_ABBR_ORDER.index(day) if day in DAY_ABBR_ORDER else -1
                if day_idx_loop < today_idx:
                    st.warning(f"Carryover from {day}")
                if day_idx_loop > today_idx:
                    st.info(f"{day} — unlocked for early prep")
                day_state = st.session_state.cycle_team_tracking.get(day, {})
                day_facs = cycle_facilities.get(day, [])
                done = sum(1 for f in day_facs if stage_counts(day_state.get(f, {}))[0] == len(CYCLE_STAGE_ORDER))
                st.markdown(f"**{day}**: {done} of {len(day_facs)} complete")
                for facility in day_facs:
                    if facility not in day_state:
                        day_state[facility] = {s: "" for s in CYCLE_STAGE_ORDER}
                    render_cycle_facility(day, facility, day_state[facility])
    
    # ============ TAB 3: HIGH DOLLAR TRACKING ============
    with cycle_tab3:
        dollar_facilities = _cycle_facilities
        
        for day, facs in dollar_facilities.items():
            if day not in st.session_state.dollar_tracking:
                st.session_state.dollar_tracking[day] = {}
            for fac in facs:
                if fac not in st.session_state.dollar_tracking[day]:
                    st.session_state.dollar_tracking[day][fac] = {stage: "" for stage in CYCLE_DOLLAR_STAGE_ORDER}
        
        st.markdown("### Cycle High Dollar Tracking")
        st.caption("Track high-dollar billing stages. Facilities shown with $$ suffix.")
        today_abbr = _today_abbr()
        week_dates = get_week_dates()
        today_idx = DAY_ABBR_ORDER.index(today_abbr) if today_abbr in DAY_ABBR_ORDER else -1
        
        def get_visible_days_dollar(ts, fc):
            if today_abbr not in DAY_ABBR_ORDER:
                return DAY_ABBR_ORDER
            visible = []
            for i, d in enumerate(DAY_ABBR_ORDER):
                if i == today_idx:
                    visible.append(d)
                elif i < today_idx:
                    for f in fc.get(d, []):
                        fs = ts.get(d, {}).get(f, {})
                        if fs.get("_no_meds"):
                            continue
                        c, t = stage_counts(fs, CYCLE_DOLLAR_STAGE_ORDER)
                        if c < t:
                            visible.append(d)
                            break
            return visible
        
        visible_days = get_visible_days_dollar(st.session_state.dollar_tracking, dollar_facilities)
        if not visible_days:
            visible_days = [today_abbr] if today_abbr in DAY_ABBR_ORDER else DAY_ABBR_ORDER
        
        for unlocked in st.session_state.dollar_unlocked_days:
            if unlocked not in visible_days and unlocked in DAY_ABBR_ORDER:
                visible_days.append(unlocked)
        visible_days = sorted(set(visible_days), key=lambda d: DAY_ABBR_ORDER.index(d) if d in DAY_ABBR_ORDER else 99)
        
        future_days = DAY_ABBR_ORDER[today_idx + 1:] if today_idx >= 0 else []
        next_unlockable = next((fd for fd in future_days if fd not in st.session_state.dollar_unlocked_days), None)
        
        st.caption("Click a facility $$ to expand and mark stages complete.")
        btn_cols = st.columns(5)
        col_idx = 0
        for unlocked in st.session_state.dollar_unlocked_days:
            if col_idx < 5:
                with btn_cols[col_idx]:
                    if st.button(f"🔒 Lock {unlocked}", key=f"$relock_{unlocked}", use_container_width=True):
                        st.session_state.dollar_unlocked_days.remove(unlocked)
                        st.session_state.dollar_unlocked_days = [d for d in st.session_state.dollar_unlocked_days if DAY_ABBR_ORDER.index(d) < DAY_ABBR_ORDER.index(unlocked)]
                        save_session_to_shared()
                        st.rerun()
                col_idx += 1
        if next_unlockable and col_idx < 5:
            with btn_cols[col_idx]:
                if st.button(f"🔓 Unlock {next_unlockable}", key=f"$unlock_{next_unlockable}", use_container_width=True):
                    st.session_state.dollar_unlocked_days.append(next_unlockable)
                    save_session_to_shared()
                    st.rerun()
        
        day_tabs = st.tabs([f"{day} $$ ({week_dates.get(day, '')})" for day in visible_days])
        for day_tab, day in zip(day_tabs, visible_days):
            with day_tab:
                day_idx_loop = DAY_ABBR_ORDER.index(day) if day in DAY_ABBR_ORDER else -1
                if day_idx_loop < today_idx:
                    st.warning(f"Carryover from {day}")
                if day_idx_loop > today_idx:
                    st.info(f"{day} $$ — unlocked for early prep")
                day_state = st.session_state.dollar_tracking.get(day, {})
                day_facs = dollar_facilities.get(day, [])
                done = sum(1 for f in day_facs if day_state.get(f, {}).get("_no_meds") or stage_counts(day_state.get(f, {}), CYCLE_DOLLAR_STAGE_ORDER)[0] == len(CYCLE_DOLLAR_STAGE_ORDER))
                st.markdown(f"**{day} $$**: {done} of {len(day_facs)} complete")
                for facility in day_facs:
                    if facility not in day_state:
                        day_state[facility] = {s: "" for s in CYCLE_DOLLAR_STAGE_ORDER}
                    render_dollar_facility(day, facility, day_state[facility])

    # ============ TAB 4: CYCLE BAG COUNT ============
    with cycle_tab4:
        import uuid
        
        # Load bag count state
        bag_state = supa.load_bag_count_state()
        if "bag_batches" not in st.session_state:
            st.session_state.bag_batches = bag_state.get("batches", {})
        if "bag_counts" not in st.session_state:
            st.session_state.bag_counts = bag_state.get("counts", {})
        if "bag_unlocked_days" not in st.session_state:
            st.session_state.bag_unlocked_days = bag_state.get("unlocked_days", [])
        if "bag_completed_days" not in st.session_state:
            st.session_state.bag_completed_days = bag_state.get("completed_days", [])
        
        st.markdown("### Cycle Bag Count")
        st.caption("Track bag counts and census per batch for each facility.")
        
        today_abbr = _today_abbr()
        week_dates = get_week_dates()
        today_idx = DAY_ABBR_ORDER.index(today_abbr) if today_abbr in DAY_ABBR_ORDER else -1
        
        # Determine visible days (entire week Mon-Fri unless marked complete + unlocked future)
        def get_visible_days_bag():
            if today_abbr not in DAY_ABBR_ORDER:
                return DAY_ABBR_ORDER
            visible = []
            completed = st.session_state.bag_completed_days
            for i, d in enumerate(DAY_ABBR_ORDER):
                # Show all days Mon-Fri unless marked complete
                if d not in completed:
                    visible.append(d)
            return visible
        
        visible_days = get_visible_days_bag()
        for unlocked in st.session_state.bag_unlocked_days:
            if unlocked not in visible_days and unlocked in DAY_ABBR_ORDER:
                visible_days.append(unlocked)
        visible_days = sorted(set(visible_days), key=lambda d: DAY_ABBR_ORDER.index(d) if d in DAY_ABBR_ORDER else 99)
        
        future_days = DAY_ABBR_ORDER[today_idx + 1:] if today_idx >= 0 else []
        next_unlockable = next((fd for fd in future_days if fd not in st.session_state.bag_unlocked_days), None)
        
        # Day Complete / Lock/Unlock buttons
        st.markdown("#### Day Controls")
        btn_cols = st.columns(5)
        col_idx = 0
        
        # Show "Day Complete" buttons for past days that are visible
        past_visible = [d for d in visible_days if DAY_ABBR_ORDER.index(d) < today_idx]
        for day in past_visible:
            if col_idx < 5:
                with btn_cols[col_idx]:
                    if st.button(f"✅ {day} ({week_dates.get(day, '')}) Complete", key=f"bag_complete_{day}", use_container_width=True):
                        if day not in st.session_state.bag_completed_days:
                            st.session_state.bag_completed_days.append(day)
                        supa.save_bag_count_state({
                            "batches": st.session_state.bag_batches,
                            "counts": st.session_state.bag_counts,
                            "unlocked_days": st.session_state.bag_unlocked_days,
                            "completed_days": st.session_state.bag_completed_days,
                        })
                        st.rerun()
                col_idx += 1
        
        # Show unlock button for future days
        if next_unlockable and col_idx < 5:
            with btn_cols[col_idx]:
                if st.button(f"🔓 Unlock {next_unlockable} ({week_dates.get(next_unlockable, '')})", key=f"bag_unlock_{next_unlockable}", use_container_width=True):
                    st.session_state.bag_unlocked_days.append(next_unlockable)
                    supa.save_bag_count_state({
                        "batches": st.session_state.bag_batches,
                        "counts": st.session_state.bag_counts,
                        "unlocked_days": st.session_state.bag_unlocked_days,
                        "completed_days": st.session_state.bag_completed_days,
                    })
                    st.rerun()
                col_idx += 1
        
        # Show lock buttons for unlocked future days
        for unlocked in st.session_state.bag_unlocked_days:
            if col_idx < 5:
                with btn_cols[col_idx]:
                    if st.button(f"🔒 Lock {unlocked}", key=f"bag_relock_{unlocked}", use_container_width=True):
                        st.session_state.bag_unlocked_days.remove(unlocked)
                        st.session_state.bag_unlocked_days = [d for d in st.session_state.bag_unlocked_days if DAY_ABBR_ORDER.index(d) < DAY_ABBR_ORDER.index(unlocked)]
                        supa.save_bag_count_state({
                            "batches": st.session_state.bag_batches,
                            "counts": st.session_state.bag_counts,
                            "unlocked_days": st.session_state.bag_unlocked_days,
                            "completed_days": st.session_state.bag_completed_days,
                        })
                        st.rerun()
                col_idx += 1
        
        # Manual save button (in case auto-save failed)
        save_col1, save_col2 = st.columns([1, 3])
        with save_col1:
            if st.button("💾 Save All to Database", key="manual_save_bags", use_container_width=True):
                supa.save_bag_count_state({
                    "batches": st.session_state.bag_batches,
                    "counts": st.session_state.bag_counts,
                    "unlocked_days": st.session_state.bag_unlocked_days,
                    "completed_days": st.session_state.bag_completed_days,
                })
                st.success("✅ Saved to database!")
        with save_col2:
            if supa.using_supabase():
                st.caption("🟢 Connected to Supabase")
            else:
                st.caption("🔴 Supabase not connected — data in session only")
        
        # Batch Management in expander
        with st.expander("⚙️ Manage Batches (per facility)", expanded=False):
            st.caption("Each facility has its own batches. Add/remove batches here.")
            
            bag_facilities = _cycle_facilities
            all_facs = []
            for day_facs in bag_facilities.values():
                all_facs.extend(day_facs)
            all_facs = sorted(set(all_facs))
            
            if all_facs:
                selected_fac = st.selectbox("Select Facility", all_facs, key="batch_mgmt_fac")
                
                # Show current batches for this facility
                current_batches = st.session_state.bag_batches.get(selected_fac, [])
                
                if current_batches:
                    st.markdown(f"**Current batches for {selected_fac}:**")
                    for i, batch in enumerate(current_batches):
                        bc1, bc2 = st.columns([3, 1])
                        with bc1:
                            st.text(f"• {batch['name']}")
                        with bc2:
                            if st.button("🗑️", key=f"del_batch_{selected_fac}_{batch['id']}", help="Remove batch"):
                                st.session_state.bag_batches[selected_fac] = [b for b in current_batches if b['id'] != batch['id']]
                                supa.save_bag_count_state({
                                    "batches": st.session_state.bag_batches,
                                    "counts": st.session_state.bag_counts,
                                    "unlocked_days": st.session_state.bag_unlocked_days,
                                    "completed_days": st.session_state.bag_completed_days,
                                })
                                st.rerun()
                else:
                    st.info(f"No batches defined for {selected_fac} yet.")
                
                # Add new batch
                with st.form(f"add_batch_form_{selected_fac}", clear_on_submit=True):
                    new_batch_name = st.text_input("New Batch Name", placeholder="e.g., A Wing, Main Building")
                    if st.form_submit_button("➕ Add Batch", use_container_width=True):
                        if new_batch_name.strip():
                            if selected_fac not in st.session_state.bag_batches:
                                st.session_state.bag_batches[selected_fac] = []
                            st.session_state.bag_batches[selected_fac].append({
                                "name": new_batch_name.strip(),
                                "id": str(uuid.uuid4())[:8]
                            })
                            supa.save_bag_count_state({
                                "batches": st.session_state.bag_batches,
                                "counts": st.session_state.bag_counts,
                                "unlocked_days": st.session_state.bag_unlocked_days,
                                "completed_days": st.session_state.bag_completed_days,
                            })
                            st.rerun()
        
        st.divider()
        
        # Day tabs for bag count entry
        if visible_days:
            day_tabs = st.tabs([f"📦 {day} ({week_dates.get(day, '')})" for day in visible_days])
            for day_tab, day in zip(day_tabs, visible_days):
                with day_tab:
                    day_idx_loop = DAY_ABBR_ORDER.index(day) if day in DAY_ABBR_ORDER else -1
                    if day_idx_loop < today_idx - 2:
                        st.warning(f"Viewing older day: {day}")
                    if day_idx_loop > today_idx:
                        st.info(f"{day} — unlocked for early prep")
                    
                    day_facs = _cycle_facilities.get(day, [])
                    
                    if not day_facs:
                        st.info(f"No facilities scheduled for {day}")
                        continue
                    
                    # Initialize day in counts if needed
                    if day not in st.session_state.bag_counts:
                        st.session_state.bag_counts[day] = {}
                    
                    # Calculate totals for the day
                    day_total_bags = 0
                    day_total_census = 0
                    
                    for facility in day_facs:
                        batches = st.session_state.bag_batches.get(facility, [])
                        
                        if facility not in st.session_state.bag_counts[day]:
                            st.session_state.bag_counts[day][facility] = {}
                        
                        fac_counts = st.session_state.bag_counts[day][facility]
                        
                        # Calculate facility totals
                        fac_total_bags = sum(fac_counts.get(b['id'], {}).get('bags', 0) or 0 for b in batches)
                        fac_total_census = sum(fac_counts.get(b['id'], {}).get('census', 0) or 0 for b in batches)
                        day_total_bags += fac_total_bags
                        day_total_census += fac_total_census
                        
                        with st.expander(f"**{facility}** — {len(batches)} batch(es) | Bags: {fac_total_bags} | Census: {fac_total_census}", expanded=False):
                            if not batches:
                                st.warning(f"No batches defined for {facility}. Add batches in 'Manage Batches' above.")
                            else:
                                # Column headers
                                hdr1, hdr2, hdr3 = st.columns([2, 1, 1])
                                with hdr1:
                                    st.caption("Batch")
                                with hdr2:
                                    st.caption("Bags")
                                with hdr3:
                                    st.caption("Census")
                                
                                # Create a grid for batch entry
                                for batch in batches:
                                    batch_id = batch['id']
                                    batch_name = batch['name']
                                    
                                    if batch_id not in fac_counts:
                                        fac_counts[batch_id] = {'bags': None, 'census': None}
                                    
                                    col1, col2, col3 = st.columns([2, 1, 1])
                                    with col1:
                                        st.markdown(f"**{batch_name}**")
                                    with col2:
                                        new_bags = st.number_input(
                                            "Bags",
                                            min_value=0,
                                            value=fac_counts[batch_id].get('bags') or 0,
                                            key=f"bags_{day}_{facility}_{batch_id}",
                                            label_visibility="collapsed"
                                        )
                                        if new_bags != fac_counts[batch_id].get('bags'):
                                            fac_counts[batch_id]['bags'] = new_bags
                                            supa.save_bag_count_state({
                                                "batches": st.session_state.bag_batches,
                                                "counts": st.session_state.bag_counts,
                                                "unlocked_days": st.session_state.bag_unlocked_days,
                                                "completed_days": st.session_state.bag_completed_days,
                                            })
                                    with col3:
                                        new_census = st.number_input(
                                            "Census",
                                            min_value=0,
                                            value=fac_counts[batch_id].get('census') or 0,
                                            key=f"census_{day}_{facility}_{batch_id}",
                                            label_visibility="collapsed"
                                        )
                                        if new_census != fac_counts[batch_id].get('census'):
                                            fac_counts[batch_id]['census'] = new_census
                                            supa.save_bag_count_state({
                                                "batches": st.session_state.bag_batches,
                                                "counts": st.session_state.bag_counts,
                                                "unlocked_days": st.session_state.bag_unlocked_days,
                                                "completed_days": st.session_state.bag_completed_days,
                                            })
                                
                                # Show facility totals
                                st.markdown(f"**Total: {fac_total_bags} bags, {fac_total_census} census**")
                    
                    # Day summary
                    st.divider()
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Facilities", len(day_facs))
                    m2.metric("Total Bags", day_total_bags)
                    m3.metric("Total Census", day_total_census)

# --- PAGE: Facility Management ---
if current_page == "Facility Management":
    st.markdown("### Facility Management")
    
    # Load current config
    current_config = load_facilities_config()
    week_num = get_current_week_number()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Two columns: Add facility and Admin Override
    add_col, override_col = st.columns(2)
    
    with add_col:
        st.markdown("#### ➕ Add Facility")
        with st.form("add_facility_form", clear_on_submit=True):
            new_facility_name = st.text_input("Name", placeholder="e.g., Sunny Acres")
            c1, c2, c3 = st.columns(3)
            with c1:
                new_facility_day = st.selectbox("Day", DAY_ABBR_ORDER)
            with c2:
                new_facility_freq = st.selectbox("Freq", FREQUENCY_OPTIONS)
            with c3:
                new_start_date = st.date_input("Start", value=datetime.now())
            
            if st.form_submit_button("Add", use_container_width=True):
                if new_facility_name.strip():
                    fname = new_facility_name.strip()
                    existing_names = [f["name"] for f in current_config.get(new_facility_day, [])]
                    if fname not in existing_names:
                        if new_facility_day not in current_config:
                            current_config[new_facility_day] = []
                        new_fac = {"name": fname, "frequency": new_facility_freq}
                        if new_facility_freq != "Weekly":
                            new_fac["start_date"] = new_start_date.strftime("%Y-%m-%d")
                        current_config[new_facility_day].append(new_fac)
                        save_facilities_config(current_config)
                        st.rerun()
    
    with override_col:
        st.markdown("#### 🔐 Admin Override")
        st.caption("Mark all facilities complete or undo")
        
        # Track last override for undo
        if "last_override" not in st.session_state:
            st.session_state.last_override = None
        
        with st.form("admin_override_form"):
            override_date = st.date_input("Date", value=datetime.now() - timedelta(days=1))
            override_day = override_date.strftime("%a")[:3]
            override_type = st.radio("Apply to:", ["Cycle Tracking", f"{DOLLAR_DISPLAY} Tracking", "Both"], horizontal=True)
            
            c1, c2 = st.columns(2)
            with c1:
                do_complete = st.form_submit_button("✓ Mark Complete", use_container_width=True)
            with c2:
                do_undo = st.form_submit_button("↩ Undo/Clear", use_container_width=True)
            
            if do_complete or do_undo:
                override_date_str = override_date.strftime("%Y-%m-%d")
                facs_for_day = [f["name"] for f in current_config.get(override_day, [])]
                
                if do_complete:
                    # Mark all complete
                    if override_type in ["Cycle Tracking", "Both"]:
                        if "cycle_team_tracking" not in st.session_state:
                            st.session_state.cycle_team_tracking = {}
                        if override_day not in st.session_state.cycle_team_tracking:
                            st.session_state.cycle_team_tracking[override_day] = {}
                        for fac in facs_for_day:
                            st.session_state.cycle_team_tracking[override_day][fac] = {
                                stage: "ADMIN" for stage in CYCLE_STAGE_ORDER
                            }
                            log_stage_to_excel(fac, "ADMIN OVERRIDE", f"ALL ({override_date_str})")
                    
                    if override_type in [f"{DOLLAR_DISPLAY} Tracking", "Both"]:
                        if "dollar_tracking" not in st.session_state:
                            st.session_state.dollar_tracking = {}
                        if override_day not in st.session_state.dollar_tracking:
                            st.session_state.dollar_tracking[override_day] = {}
                        for fac in facs_for_day:
                            st.session_state.dollar_tracking[override_day][fac] = {
                                stage: "ADMIN" for stage in CYCLE_DOLLAR_STAGE_ORDER
                            }
                            log_stage_to_excel(f"{fac} $$", "ADMIN OVERRIDE", f"ALL ({override_date_str})")
                    
                    st.session_state.last_override = {"day": override_day, "type": override_type, "date": override_date_str}
                    st.success(f"Marked {len(facs_for_day)} facilities complete for {override_day}")
                
                elif do_undo:
                    # Clear/reset the day
                    if override_type in ["Cycle Tracking", "Both"]:
                        if override_day in st.session_state.get("cycle_team_tracking", {}):
                            for fac in facs_for_day:
                                st.session_state.cycle_team_tracking[override_day][fac] = {
                                    stage: "" for stage in CYCLE_STAGE_ORDER
                                }
                            log_stage_to_excel(f"{override_day} ALL", "UNDO/CLEAR", override_date_str)
                    
                    if override_type in [f"{DOLLAR_DISPLAY} Tracking", "Both"]:
                        if override_day in st.session_state.get("dollar_tracking", {}):
                            for fac in facs_for_day:
                                st.session_state.dollar_tracking[override_day][fac] = {
                                    stage: "" for stage in CYCLE_DOLLAR_STAGE_ORDER
                                }
                            log_stage_to_excel(f"{override_day} ALL $$", "UNDO/CLEAR", override_date_str)
                    
                    st.session_state.last_override = None
                    st.warning(f"Cleared {len(facs_for_day)} facilities for {override_day}")
                
                # Persist to Supabase/shared state
                save_shared_state({
                    "cycle_team_tracking": st.session_state.get("cycle_team_tracking", {}),
                    "dollar_tracking": st.session_state.get("dollar_tracking", {}),
                    "unlocked_days": st.session_state.get("unlocked_days", []),
                    "dollar_unlocked_days": st.session_state.get("dollar_unlocked_days", []),
                })
                st.rerun()
    
    st.divider()
    
    # Compact frequency legend
    st.caption(f"Week {week_num} · ✓=active · Weekly | Every 2wk (from start) | Every 4wk (from start)")
    
    # Edit/Remove facilities - compact view
    for day in DAY_ABBR_ORDER:
        day_facilities = current_config.get(day, [])
        with st.expander(f"**{day}** ({len(day_facilities)})", expanded=False):
            if not day_facilities:
                st.caption("No facilities.")
            else:
                for i, fac_data in enumerate(list(day_facilities)):
                    fac_name = fac_data["name"]
                    fac_freq = fac_data.get("frequency", "Weekly")
                    fac_start = fac_data.get("start_date")
                    is_active = facility_active_this_week(fac_freq, fac_start)
                    ind = "✓" if is_active else "○"
                    
                    # Single compact row
                    cols = st.columns([0.3, 2, 1, 1, 0.8, 0.4])
                    with cols[0]:
                        st.write(ind)
                    with cols[1]:
                        st.write(fac_name)
                    with cols[2]:
                        new_freq = st.selectbox("F", FREQUENCY_OPTIONS, 
                            index=FREQUENCY_OPTIONS.index(fac_freq) if fac_freq in FREQUENCY_OPTIONS else 0,
                            key=f"f_{day}_{i}", label_visibility="collapsed")
                        if new_freq != fac_freq:
                            current_config[day][i]["frequency"] = new_freq
                            if new_freq != "Weekly" and not fac_start:
                                current_config[day][i]["start_date"] = today_str
                            save_facilities_config(current_config)
                            st.rerun()
                    with cols[3]:
                        if fac_freq != "Weekly":
                            try:
                                cur_date = datetime.strptime(fac_start, "%Y-%m-%d").date() if fac_start else datetime.now().date()
                            except:
                                cur_date = datetime.now().date()
                            new_date = st.date_input("D", value=cur_date, key=f"d_{day}_{i}", label_visibility="collapsed")
                            if new_date.strftime("%Y-%m-%d") != fac_start:
                                current_config[day][i]["start_date"] = new_date.strftime("%Y-%m-%d")
                                save_facilities_config(current_config)
                                st.rerun()
                        else:
                            st.caption("—")
                    with cols[4]:
                        move_opts = ["—"] + [d for d in DAY_ABBR_ORDER if d != day]
                        move_to = st.selectbox("M", move_opts, key=f"m_{day}_{i}", label_visibility="collapsed")
                        if move_to != "—":
                            fac_to_move = current_config[day].pop(i)
                            if move_to not in current_config:
                                current_config[move_to] = []
                            current_config[move_to].append(fac_to_move)
                            save_facilities_config(current_config)
                            st.rerun()
                    with cols[5]:
                        if st.button("🗑", key=f"x_{day}_{i}"):
                            current_config[day].pop(i)
                            save_facilities_config(current_config)
                            st.rerun()

# --- PAGE: Data Explorer ---
if current_page == "Data Explorer":
    st.markdown("### Demo data explorer")
    st.caption("Local prototype data Turner can edit immediately.")
    st.code(DATA_FILE.read_text(), language="json")

# --- PAGE: User Management (admin only) ---
if current_page == "User Management" and is_admin_user():
    st.markdown("### 👥 User Management")
    st.caption("Add, edit, or remove users. Manage permissions.")
    
    # Load current config
    with open(CONFIG_PATH) as f:
        user_config = yaml.safe_load(f)
    
    users = user_config.get("credentials", {}).get("usernames", {})
    
    # Add new user
    st.markdown("#### Add New User")
    with st.form("add_user_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_username = st.text_input("Username", placeholder="e.g., jsmith")
            new_display = st.text_input("Display Name", placeholder="e.g., John Smith")
        with col2:
            new_password = st.text_input("Password", type="password")
            new_role = st.selectbox("Role", ["user", "admin"])
        
        if st.form_submit_button("➕ Add User", use_container_width=True):
            if new_username and new_password and new_display:
                if len(new_password) < 6:
                    st.error("Password must be at least 6 characters")
                elif new_username in users:
                    st.error(f"Username '{new_username}' already exists")
                else:
                    import bcrypt
                    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                    user_config["credentials"]["usernames"][new_username] = {
                        "name": new_display,
                        "password": hashed,
                        "role": new_role
                    }
                    save_config(user_config)
                    st.success(f"Added user '{new_username}'")
                    st.rerun()
            else:
                st.warning("Please fill in all fields")
    
    st.divider()
    
    # List existing users
    st.markdown("#### Current Users")
    for username, user_data in users.items():
        with st.expander(f"**{user_data.get('name', username)}** (@{username}) — {user_data.get('role', 'user')}"):
            col1, col2, col3 = st.columns([2, 1, 1])
            
            with col1:
                # Change password
                new_pwd = st.text_input("New Password", type="password", key=f"pwd_{username}", placeholder="Leave blank to keep")
                if new_pwd:
                    if st.button("Update Password", key=f"update_pwd_{username}"):
                        if len(new_pwd) >= 6:
                            import bcrypt
                            hashed = bcrypt.hashpw(new_pwd.encode(), bcrypt.gensalt()).decode()
                            user_config["credentials"]["usernames"][username]["password"] = hashed
                            save_config(user_config)
                            st.success("Password updated")
                            st.rerun()
                        else:
                            st.error("Min 6 characters")
            
            with col2:
                # Change role
                current_role = user_data.get("role", "user")
                new_role_sel = st.selectbox("Role", ["user", "admin"], 
                                       index=0 if current_role == "user" else 1,
                                       key=f"role_{username}")
                if new_role_sel != current_role:
                    if st.button("Update Role", key=f"update_role_{username}"):
                        user_config["credentials"]["usernames"][username]["role"] = new_role_sel
                        save_config(user_config)
                        st.success(f"Role updated to {new_role_sel}")
                        st.rerun()
            
            with col3:
                # Delete user (can't delete yourself)
                if username != st.session_state.get("username"):
                    if st.button("🗑️ Delete", key=f"del_{username}", type="secondary"):
                        del user_config["credentials"]["usernames"][username]
                        save_config(user_config)
                        st.success(f"Deleted user '{username}'")
                        st.rerun()
                else:
                    st.caption("(You)")
    
    st.divider()
    
    # --- Permissions Management ---
    st.markdown("#### 🔐 Page Permissions")
    st.caption("Control which pages each role or user can access.")
    
    permissions = user_config.get("permissions", {"roles": {"admin": ["all"], "user": []}, "users": {}})
    
    # Role permissions
    st.markdown("##### Role Defaults")
    role_col1, role_col2 = st.columns(2)
    
    with role_col1:
        st.markdown("**Admin Role**")
        st.caption("Admins have access to all pages by default.")
    
    with role_col2:
        st.markdown("**User Role**")
        current_user_perms = permissions.get("roles", {}).get("user", [])
        
        # Checkboxes for each page
        new_user_perms = []
        for page in ALL_PAGES:
            if page == "User Management":
                continue  # Never give regular users access to user management
            checked = st.checkbox(page, value=(page in current_user_perms), key=f"perm_user_{page}")
            if checked:
                new_user_perms.append(page)
        
        if new_user_perms != current_user_perms:
            if st.button("Save User Role Permissions"):
                if "permissions" not in user_config:
                    user_config["permissions"] = {"roles": {}, "users": {}}
                if "roles" not in user_config["permissions"]:
                    user_config["permissions"]["roles"] = {}
                user_config["permissions"]["roles"]["user"] = new_user_perms
                save_config(user_config)
                st.success("User role permissions updated!")
                st.rerun()
    
    st.divider()
    
    # Per-user permission overrides
    st.markdown("##### Per-User Overrides")
    st.caption("Override role permissions for specific users.")
    
    user_overrides = permissions.get("users", {})
    
    for username, user_data in users.items():
        user_override = user_overrides.get(username)
        role = user_data.get("role", "user")
        
        with st.expander(f"**{user_data.get('name', username)}** (@{username}) — {role}"):
            has_override = user_override is not None
            enable_override = st.checkbox("Enable custom permissions", value=has_override, key=f"override_enable_{username}")
            
            if enable_override:
                current_perms = user_override if user_override else []
                new_perms = []
                
                cols = st.columns(3)
                for i, page in enumerate(ALL_PAGES):
                    if page == "User Management" and role != "admin":
                        continue
                    with cols[i % 3]:
                        checked = st.checkbox(page, value=(page in current_perms), key=f"perm_{username}_{page}")
                        if checked:
                            new_perms.append(page)
                
                if st.button(f"Save {username}'s permissions", key=f"save_perm_{username}"):
                    if "permissions" not in user_config:
                        user_config["permissions"] = {"roles": {}, "users": {}}
                    if "users" not in user_config["permissions"]:
                        user_config["permissions"]["users"] = {}
                    user_config["permissions"]["users"][username] = new_perms
                    save_config(user_config)
                    st.success(f"Permissions for {username} updated!")
                    st.rerun()
            else:
                if has_override:
                    if st.button(f"Remove override (use role defaults)", key=f"remove_override_{username}"):
                        del user_config["permissions"]["users"][username]
                        save_config(user_config)
                        st.success(f"Override removed for {username}")
                        st.rerun()
                else:
                    st.caption(f"Using {role} role defaults")

st.divider()
last_updated = datetime.fromtimestamp(DATA_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
st.caption(f"Ozark LTC Rx Ops prototype ready · Data source: {DATA_FILE} · Last data update: {last_updated}")
