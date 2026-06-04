from __future__ import annotations

import streamlit as st

from dashboard.db import init_db
from dashboard.auth import logout_admin, is_admin_authenticated
from dashboard.public.submit import render_submit_page
from dashboard.page_registry import RESULTS_PAGE
from dashboard.public.about import render_about_page
from dashboard.admin.home import render_admin_home_page
from dashboard.admin.sessions import render_admin_sessions_page
from dashboard.admin.participants import render_admin_participants_page
from dashboard.admin.submissions import render_admin_submissions_page
from dashboard.admin.processing import render_admin_session_processing_page
from dashboard.admin.exports import render_admin_session_exports_page

st.set_page_config(
    page_title="Scenario MCDM Stakeholder Polling",
    page_icon="🗳️",
    layout="wide"
)

init_db()

public_pages = [
    st.Page(render_submit_page, title="Submit Preference", icon="🗳️", default=True, url_path="submit"),
    RESULTS_PAGE,
    st.Page(render_about_page, title="About", icon="ℹ️", url_path="about")
]

if is_admin_authenticated():
    admin_pages = [
        st.Page(render_admin_home_page, title="Admin Home", icon="👑", url_path="admin"),
        st.Page(render_admin_sessions_page, title="Manage Sessions", icon="🗂️", url_path="admin-sessions"),
        st.Page(render_admin_participants_page, title="Manage Participants", icon="👥", url_path="admin-participants"),
        st.Page(render_admin_submissions_page, title="Manage Submissions", icon="📥", url_path="admin-submissions"),
        st.Page(render_admin_session_processing_page, title="Session Processing", icon="🧾", url_path="admin-processing"),
        st.Page(render_admin_session_exports_page, title="Exports", icon="📦", url_path="admin-exports",)
    ]

    navigation = {
        "Public": public_pages,
        "Moderator": admin_pages,
    }

    with st.sidebar:
        if st.button("Log Out"):
            logout_admin()
            st.rerun()
else:
    navigation = {
        "Public": public_pages,
        "Moderator": [st.Page(render_admin_home_page, title="Moderator Login", icon="🔐", url_path="admin")],
    }

pg = st.navigation(navigation)
pg.run()