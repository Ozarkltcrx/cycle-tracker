"""
Auth persistence fix for Ozark LTC Rx Cycle Tracker.

This module provides Supabase-backed user credential storage so that
user accounts persist across Streamlit Cloud restarts.

Add these functions to app.py and supabase_client.py to fix the issue.
"""

# ============================================================================
# ADD TO supabase_client.py (at the end, before any existing functions)
# ============================================================================

SUPABASE_CLIENT_ADDITIONS = '''
# ── User Auth Config (Supabase-backed persistence) ──────────────────────────

def load_auth_config_db() -> dict | None:
    """Load user auth config from Supabase. Returns None if not available."""
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
    """Save user auth config to Supabase for persistence."""
    if _USE_SUPABASE:
        try:
            _supabase_client.table("users_config").upsert(
                {"key": "auth_config", "value": config},
                on_conflict="key",
            ).execute()
        except Exception as e:
            print(f"Warning: Failed to save auth config to Supabase: {e}")
'''

# ============================================================================
# REPLACE the auth section in app.py (around lines 689-712) with this:
# ============================================================================

APP_PY_AUTH_REPLACEMENT = '''
# --- Authentication (with Supabase persistence) ---
CONFIG_PATH = APP_DIR / "config.yaml"

def load_merged_config() -> dict:
    """Load config from Supabase (if available), merged with local config.yaml."""
    # Start with local config.yaml as base
    local_config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            local_config = yaml.safe_load(f) or {}
    
    # Try to load from Supabase
    db_config = supa.load_auth_config_db()
    
    if db_config:
        # Supabase config takes precedence for credentials
        # But we keep local cookie settings as fallback
        merged = local_config.copy()
        if "credentials" in db_config:
            merged["credentials"] = db_config["credentials"]
        if "permissions" in db_config:
            merged["permissions"] = db_config["permissions"]
        return merged
    
    return local_config


def save_config(config: dict) -> None:
    """Save config to both Supabase (for persistence) and local file."""
    # Save to Supabase first (this persists across restarts)
    supa.save_auth_config_db(config)
    
    # Also save locally (for immediate use and backup)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


# Load merged config
config = load_merged_config()

if config:
    authenticator = stauth.Authenticate(
        config['credentials'],
        config['cookie']['name'],
        config['cookie']['key'],
        config['cookie']['expiry_days'],
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
    st.error("No authentication config found. Please set up config.yaml")
    st.stop()
# --- End Authentication ---
'''

# ============================================================================
# REPLACE all occurrences of saving config in User Management section
# Change: with open(CONFIG_PATH, "w") as f: yaml.dump(user_config, f, ...)
# To: save_config(user_config)
# ============================================================================

USER_MANAGEMENT_FIX_NOTE = '''
In the User Management section (around lines 1744, 1768, 1784, 1794, 1837, 1877, 1885):

Replace all instances of:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(user_config, f, default_flow_style=False)

With:
    save_config(user_config)

This ensures all user changes are saved to Supabase for persistence.
'''

print("Auth fix instructions generated. See the strings above for the code changes needed.")
