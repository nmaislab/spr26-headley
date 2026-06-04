from __future__ import annotations

import streamlit as st

from dashboard.auth import render_admin_login

def render_admin_session_exports_page() -> None:
    if not render_admin_login():
        return