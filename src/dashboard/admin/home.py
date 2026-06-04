from __future__ import annotations

import streamlit as st

from dashboard.auth import render_admin_login

def render_admin_home_page() -> None:
    if not render_admin_login():
        return
    
    st.title("👑 Moderator Home")
    st.write("Use the moderator pages to manage sessions, participants, submissions, preprocessing, and exports.")