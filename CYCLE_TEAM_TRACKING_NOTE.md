# Cycle Team Tracking Updates

Refined the Streamlit **Cycle Team Tracking** workflow in `app.py`.

## What changed
- Fixed stage label typo: **Trying** → **Traying**
- Weekday visibility is now smarter:
  - shows **today's facilities**
  - also shows **prior-day incomplete facilities** as carryover
  - hides prior days that are fully complete
- Added a new top-level tab: **Status Overview**
  - today's facility progress summary
  - overdue / carryover section for incomplete prior days
  - recent audit-log visibility
- Added Excel audit logging to:
  - `data/cycle_log.xlsx`
  - records: Facility, Stage, Initials, Date, Time

## How Turner can test it
1. From the project folder, run: `streamlit run app.py`
2. Open **Cycle Team Tracking** and confirm only today's day tab plus overdue prior days appear.
3. Mark a stage complete with initials.
4. Confirm the facility progress updates and a row is appended to `data/cycle_log.xlsx`.
5. Open **Status Overview** to review today's board and any overdue carryover facilities.

## Validation run
- Syntax checked with: `python3 -m py_compile app.py`
- Verified changed files exist: `app.py`, `data/cycle_log.xlsx`, `CYCLE_TEAM_TRACKING_NOTE.md`
