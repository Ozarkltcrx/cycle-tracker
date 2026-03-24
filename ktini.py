#!/usr/bin/env python3
"""
KTini's interface to Command Center.
Used by KTini to post status updates, alerts, and messages for Turner.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

STATE_FILE = Path(__file__).parent / "state.json"


def default_state():
    return {
        "projects": {},
        "messages": [],
        "ktini_updates": [],
        "quick_notes": [],
        "pinned": [],
        "thorpe_typing": False,
    }


def load_state():
    state = default_state()
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text()))
    return state


def save_state(state):
    full_state = default_state()
    full_state.update(state)
    STATE_FILE.write_text(json.dumps(full_state, indent=2))


def post(text, msg_type="update"):
    state = load_state()
    state["ktini_updates"].insert(0, {
        "timestamp": datetime.now().isoformat(),
        "type": msg_type,
        "text": text,
        "from": "ktini",
    })
    save_state(state)
    print(f"✅ KTini {msg_type} posted: {text[:80]}...")


def show_all(limit=10):
    state = load_state()
    updates = state.get("ktini_updates", [])[:limit]

    print("👷‍♀️ Recent KTini updates:\n")
    for msg in reversed(updates):
        ts = datetime.fromisoformat(msg["timestamp"]).strftime("%m/%d %H:%M")
        msg_type = msg.get("type", "update").upper()
        print(f"👷‍♀️ [{ts}] {msg_type}: {msg['text']}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python ktini.py all                  - Show recent KTini updates")
        print("  python ktini.py post TEXT            - Post a general update")
        print("  python ktini.py update TEXT          - Post a status update")
        print("  python ktini.py alert TEXT           - Post an alert")
        print("  python ktini.py message TEXT         - Post a direct message")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "all":
        show_all()
    elif cmd in {"post", "update", "alert", "message"} and len(sys.argv) > 2:
        msg_type = "update" if cmd == "post" else cmd
        post(" ".join(sys.argv[2:]), msg_type=msg_type)
    else:
        print("Unknown command")
        sys.exit(1)
