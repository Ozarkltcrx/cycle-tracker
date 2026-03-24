# Run Today

## Launch the Missouri LTC pharmacy prototype

From the project folder:

```bash
cd /Users/turner/.openclaw/workspace/projects/command-center
streamlit run app.py
```

If `streamlit` is not on your PATH, try:

```bash
python3 -m streamlit run app.py
```

## What you should see

- **Missouri LTC Ops Center** at the top
- Tabs for:
  - **Today / Daily Ops**
  - **Facility Attention Queue**
  - **ADT Tracker**
  - **Cycle Fill / Delivery / E-Kits**
  - **Data Explorer**

## Demo data

The app reads local seed data from:

```text
data/mo_ltc_demo.json
```

You can edit that file and refresh the app to change facilities, admissions, cycle fill, routes, and emergency-kit examples.

## Quick validation

```bash
python3 -m py_compile app.py
python3 -m streamlit run app.py --server.headless true --server.port 8511
```

Then open:

```text
http://localhost:8511
```
