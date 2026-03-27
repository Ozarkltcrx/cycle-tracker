# Task Spec: QA Section with BNDD License Tracking

**Date:** 2026-03-27  
**Requested by:** Turner  
**Priority:** Normal

---

## Overview

Add a new **QA** section to the left sidebar navigation. Within QA, create two tabs:
1. **Dashboard** — blank placeholder for future use
2. **BNDD License** — license tracking page for facilities

---

## Requirements

### Navigation
- Add "QA" to `ALL_PAGES` list in `app.py`
- Add QA section between existing pages (suggest after "Pharmacy Management")
- Apply same permission pattern as other pages

### QA > Dashboard Tab
- Blank page with placeholder text: "QA Dashboard - Coming Soon"
- No functionality needed yet

### QA > BNDD License Tab

#### Display
- Table/dataframe with columns:
  - **Facility** (name)
  - **License Number** (text)
  - **Expiration Date** (date)
  - **Actions** (renew button)

#### Data Source
- Facility dropdown should pull from master_facilities list (from Pharmacy Management)
- New BNDD license data stored separately (new Supabase key: `bndd_licenses`)

#### Add License
- Dropdown to select facility (from master_facilities list)
- Text input for license number
- Date picker for expiration date
- Add button

#### Edit/Renew
- Each row has a "Renew" button
- Clicking Renew opens an inline form or modal to update the expiration date
- Save updates to Supabase

#### Data Structure
```python
# Supabase key: "bndd_licenses"
# Value structure:
[
    {
        "facility": "Sunrise Manor",
        "license_number": "12345-BNDD",
        "expiration_date": "2027-03-15"
    },
    ...
]
```

---

## Implementation Notes

### supabase_client.py additions needed:
```python
def load_bndd_licenses() -> list[dict]:
    """Load BNDD license data."""
    ...

def save_bndd_licenses(licenses: list[dict]) -> None:
    """Save BNDD license data."""
    ...
```

### app.py additions:
1. Add "QA" to `ALL_PAGES`
2. Add `if current_page == "QA":` section
3. Use internal tabs for Dashboard vs BNDD License
4. Pull facility names from `st.session_state.master_facilities` or `supa.load_master_facilities()`

---

## Acceptance Criteria

- [ ] QA appears in sidebar navigation
- [ ] QA > Dashboard shows placeholder
- [ ] QA > BNDD License shows empty table initially
- [ ] Can add a new license by selecting facility from dropdown
- [ ] Can view all licenses in table format
- [ ] Renew button allows updating expiration date
- [ ] Data persists in Supabase
- [ ] Follows existing code patterns (permissions, styling, etc.)

---

## Assign To

**KTini** → distribute to **Willy** for implementation
