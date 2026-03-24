# Missouri LTC Pharmacy Domain Support

_Prototype support file for demo use. Assumptions are directional/sample content, not legal or payer advice._

## Scope
This file gives Willy a believable Missouri long-term care (LTC) operating model for a command-center prototype. It is optimized for operator workflows, triage logic, and realistic demo copy.

## Missouri-oriented assumptions

### 1) Facility mix
Use a mixed Missouri book of business:
- Urban/suburban SNFs in St. Louis, Kansas City, Springfield, and Columbia
- Rural nursing facilities with longer courier windows and fewer backup vendors
- Assisted living / memory care sites with lighter clinical complexity but higher communication variability

### 2) Operator reality in Missouri
- Many facilities are Medicaid-heavy, so service failures hurt retention even when margins are already thin.
- Rural routes are more fragile because weather, distance, and after-hours staffing can turn a normal exception into a same-day service risk.
- Admissions often arrive late in the day from hospital discharge planners, creating first-dose pressure.
- Weekend/after-hours issues are commonly driven by incomplete admit packets, missing coverage information, unavailable prescribers, controlled-substance logistics, or e-kit gaps.

### 3) Most credible workflows for the prototype
Focus the product around four daily command-center views:
1. Facility status and account risk
2. Daily risk flags / service exceptions
3. ADT queue (admissions, discharges, transfers)
4. Cycle fill + delivery + e-kit readiness

## Core workflow assumptions

### Admissions
Typical intake sequence:
1. Facility sends facesheet / demographics / room-bed / attending / allergies
2. Pharmacy builds resident profile
3. Orders are entered and clarified
4. Coverage and refill-too-soon / PA risks are checked
5. Packaging path is chosen: first dose, e-kit use, STAT courier, or next route / cycle
6. Facility gets ETA / next-step confirmation

**High-risk admit conditions**
- Admit packet incomplete
- First dose due within 4 hours
- Anticoagulant / insulin / antibiotic / seizure med on profile
- Controlled substance needed tonight
- Prescriber clarification pending
- Resident arrived after route cutoff

### Discharges
Typical discharge steps:
1. Stop future fills
2. Cancel or intercept staged deliveries
3. Identify returns / credits / rebill impact
4. Update facility and billing status

**Operator risk**
The credible dashboard should show pending credits and deliveries that may still go out after discharge notice.

### Transfers
Typical transfer types:
- Room/bed change inside same facility
- Unit change (rehab to LTC, memory care to skilled, etc.)
- Facility-to-facility transfer

**High-risk transfer conditions**
- Destination unit/facility not confirmed
- Tote/route assignment not updated
- Controlled meds need chain-of-custody handling
- Facility chart location changed but pharmacy destination did not

### Cycle fill
Typical cycle-fill blockers:
- Refill-too-soon or payer reject
- PA required
- New order not yet verified
- Drug unavailable / short supply
- Census change not reflected
- Resident discharged or on hold
- Facility requested hold but cart still scheduled

**Useful prototype logic**
- Readiness % = completed residents / scheduled residents
- A facility below 92% by late afternoon should show yellow; below 85% should show red
- Repeated issues from the same facility should raise account-risk framing, not just task framing

### Delivery board
Helpful statuses:
- queued
- packed
- staged
- dispatched
- at facility
- delivered
- delayed
- exception

**Delivery risk triggers**
- Route cutoff within 90 minutes and unresolved items remain
- STAT item not packed within target prep window
- Rural facility with weather or distance issue
- Missing proof-of-delivery for controlled meds

### E-kit / emergency box logic
Use e-kit content for urgent bridge doses, not routine poor planning.

Common credible e-kit categories for demo:
- antibiotics starter doses
- antiemetics
- hypoglycemia rescue items
- seizure rescue items
- respiratory rescue meds
- analgesics
- anticoagulant bridge / reversal support references only if appropriate to site policy

**E-kit risk triggers**
- Seal broken and not reconciled
- Expiring within 30 days
- Required item missing after overnight use
- Use not documented by facility

## Suggested prioritization logic

### Severity ladder
- **Critical**: likely patient-care impact today or controlled-substance/documentation exposure
- **High**: same-day operational failure likely without intervention
- **Medium**: needs action before next route / cycle
- **Low**: informational or trend-only

### Example scoring model for demo UI
Score 0-100 using weighted factors:
- first dose due soon: +25
- high-risk med class: +20
- admit/discharge/transfer incomplete: +15
- payer / clarification blocker: +10
- route cutoff approaching: +15
- facility repeat-offender pattern: +10
- rural/logistics fragility: +5

Suggested labels:
- 80-100 = Critical
- 60-79 = High
- 35-59 = Medium
- 0-34 = Low

## Facility scorecard fields that feel real
- census
- service level (SNF / ALF / memory care)
- cycle day
- route window
- admits last 7 days
- missing-med calls last 7 days
- unresolved rejects
- STAT deliveries this month
- discharge credits pending
- e-kit exceptions open
- overall account risk

## Missouri-friendly demo language
Use wording like:
- rural route sensitivity
- hospital discharge wave
- first-dose turnaround
- med-pass risk tonight
- discharge credit leakage
- survey-readiness exposure
- e-kit reconciliation overdue

## Guardrails / assumptions
- This demo assumes standard LTC pharmacy workflows but does not represent a specific Missouri regulation set or facility contract.
- Do not imply automated clinical decisions; frame outputs as triage/support recommendations.
- Controlled-substance and emergency-box workflows should be presented as tracked/documented operational tasks, not compliance guarantees.
- Use resident initials or masked names only in UI demos unless a fully synthetic data set is clearly labeled.

## Short handoff for Willy
- Use `missouri_ltc_demo_data.json` as the immediate seed file.
- Best first UI blocks: `facilities`, `daily_risk_flags`, `adt_events`, `cycle_fill_status`, `deliveries`, and `e_kit_status`.
- If you need a single "what matters today" view, sort `daily_risk_flags` by `priority_score` desc and group by `facility_code`.
- For credibility, surface both clinical urgency and operator friction: first-dose risk, route cutoff, missing documentation, pending credits, and e-kit reconciliation.
- Treat all content here as synthetic/demo-safe assumptions.
