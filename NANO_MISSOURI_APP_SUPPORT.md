# Nano Missouri App Support

_Date: 2026-03-11_

## What I added

### 1. Missouri LTC operator hero panel in `app.py`
Added a top-of-dashboard hero section with:
- headline: **Missouri LTC Pharmacy Operations Command Center**
- operator-focused subhead centered on first doses, cycle-fill risk, facility noise, revenue leakage, and compliance exposure
- a five-item **Today’s priority stack** written in concrete pharmacy operations language

### 2. Demo-ready morning risk board
Added six visible queue cards with practical labels and wording:
- Admissions at risk
- First doses due < 4 hrs
- Cycle-fill exceptions
- High-dollar rejects
- Controlled-substance tasks
- Pending discharge credits

These are intentionally phrased so Willy can later connect them to real data without redoing the UX language.

### 3. First-90-minutes operator workflow copy
Added a four-step workflow block for morning use:
1. Triage the morning risk board
2. Stabilize the buildings making the most noise
3. Protect cash and service together
4. Close compliance-sensitive items before noon

This gives the app a clear operating rhythm instead of generic dashboard filler.

### 4. Facility watchlist content
Added a demo table with Missouri-style LTC facility names and realistic action language:
- Maple Grove Care Center
- Ozark Ridge SNF
- St. Charles Transitional Care
- Riverbend Memory Care

Each row includes:
- segment
- risk level
- trigger
- immediate action

This is useful for demos, owner reviews, and account-health conversations.

### 5. Copy polish / hierarchy upgrades
Adjusted existing app wording to feel more like an LTC pharmacy operations cockpit:
- sidebar now says **Missouri LTC operator view**
- metrics relabeled from generic project terms to more operational language
- portfolio pulse rewritten as **Missouri LTC operating pulse**
- footer version note updated to reflect Missouri operator polish

## Why these decisions were made

The app shell already existed. Rather than rewrite structure, I added content layers Willy can implement immediately:
- stronger above-the-fold context
- clearer owner/operator prioritization
- realistic operational labels
- demo-ready facility/account language
- better distinction between urgent care risk and preventable workflow noise

## Notes for Willy

If he wants to take this one step further later, the easiest next wins are:
1. swap the demo queue values for live values from a JSON/CSV source
2. add color-coded severity chips to the facility watchlist
3. create dedicated tabs for **ADT**, **Cycle Fill**, **Revenue at Risk**, and **Compliance**
4. let each dashboard metric click through to an exception list

## Files changed
- `/Users/turner/.openclaw/workspace/projects/command-center/app.py`
- `/Users/turner/.openclaw/workspace/projects/command-center/NANO_MISSOURI_APP_SUPPORT.md`
