from __future__ import annotations

import hashlib
import hmac
import os

import streamlit as st

def hash_password(password: str) -> str:
    """
    Simple SHA-256 hash for prototype/admin gate.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def _get_secret_or_env(key: str) -> str | None:
    if key in st.secrets:
        return str(st.secrets[key])

    value = os.getenv(key)
    if value:
        return value

    return None

def get_configured_admin_password_hash() -> str | None:
    """
    Prefer ADMIN_PASSWORD_HASH.
    """
    configured_hash = _get_secret_or_env("ADMIN_PASSWORD_HASH")
    if configured_hash:
        return configured_hash

    # TODO: Remove when hosting the application
    plain_password = _get_secret_or_env("ADMIN_PASSWORD")
    if plain_password:
        return hash_password(plain_password)
    
    return None

def verify_admin_password(password: str) -> bool:
    configured_hash = get_configured_admin_password_hash()

    if not configured_hash:
        return False

    submitted_hash = hash_password(password)
    return hmac.compare_digest(submitted_hash, configured_hash)

def is_admin_authenticated() -> bool:
    return bool(st.session_state.get("admin_authenticated", False))


def logout_admin() -> None:
    st.session_state.pop("admin_authenticated", None)

def render_admin_login() -> bool:
    if is_admin_authenticated():
        return True

    st.title("🔐 Moderator Login")
    st.write("This area is restricted to the moderator/admin.")

    configured_hash = get_configured_admin_password_hash()

    if not configured_hash:
        st.error(
            "Admin password is not configured. Set ADMIN_PASSWORD_HASH or ADMIN_PASSWORD "
            "in Streamlit secrets or environment variables."
        )
        return False

    with st.form("admin_login_form"):
        password = st.text_input("Admin password", type="password")
        submitted = st.form_submit_button("Log in", type="primary")

    if submitted:
        if verify_admin_password(password):
            st.session_state["admin_authenticated"] = True
            st.success("Login successful.")
            st.rerun()
        else:
            st.error("Invalid admin password.")

    return False