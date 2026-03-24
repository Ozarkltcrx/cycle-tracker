#!/usr/bin/env python3
"""
Thorpe's interface to Command Center.
Used by Thorpe to read and respond to messages.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

STATE_FILE = Path(__file__).parent / "state.json"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"projects": {}, "messages": [], "quick_notes": [], "pinned": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def show_unread():
    """Show messages from Turner that Thorpe hasn't responded to"""
    state = load_state()
    messages = state.get("messages", [])
    
    # Find messages from Turner after last Thorpe message
    last_thorpe = None
    for msg in messages:
        if msg.get("from") == "thorpe":
            last_thorpe = msg["timestamp"]
            break
    
    unread = []
    for msg in messages:
        if msg.get("from") != "thorpe":
            if last_thorpe is None or msg["timestamp"] > last_thorpe:
                unread.append(msg)
    
    if unread:
        print(f"📬 {len(unread)} unread message(s) from Turner:\n")
        for msg in reversed(unread):
            ts = datetime.fromisoformat(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")
            print(f"[{ts}] {msg.get('type', 'note')}: {msg['text']}\n")
    else:
        print("📭 No unread messages")
    
    return unread

def typing(on=True):
    """Set Thorpe's typing indicator"""
    state = load_state()
    state["thorpe_typing"] = on
    save_state(state)
    print(f"💬 Typing indicator: {'on' if on else 'off'}")

def reply(text, msg_type="update"):
    """Post a reply from Thorpe"""
    state = load_state()
    state["thorpe_typing"] = False  # Clear typing indicator
    state["messages"].insert(0, {
        "timestamp": datetime.now().isoformat(),
        "type": msg_type,
        "text": text,
        "from": "thorpe"
    })
    save_state(state)
    print(f"✅ Reply posted: {text[:50]}...")

def show_all():
    """Show recent message history"""
    state = load_state()
    messages = state.get("messages", [])[:10]
    
    print("📜 Recent messages:\n")
    for msg in reversed(messages):
        ts = datetime.fromisoformat(msg["timestamp"]).strftime("%m/%d %H:%M")
        sender = msg.get("from", "turner").upper()
        icon = "🏈" if sender == "THORPE" else "👤"
        print(f"{icon} [{ts}] {sender}: {msg['text']}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python thorpe.py unread     - Show unread messages")
        print("  python thorpe.py all        - Show recent history")
        print("  python thorpe.py typing     - Show typing indicator")
        print("  python thorpe.py typing off - Hide typing indicator")
        print("  python thorpe.py reply TEXT - Post a reply")
        sys.exit(0)
    
    cmd = sys.argv[1]
    
    if cmd == "unread":
        show_unread()
    elif cmd == "all":
        show_all()
    elif cmd == "typing":
        on = len(sys.argv) < 3 or sys.argv[2].lower() != "off"
        typing(on)
    elif cmd == "reply" and len(sys.argv) > 2:
        reply(" ".join(sys.argv[2:]))
    else:
        print("Unknown command")
