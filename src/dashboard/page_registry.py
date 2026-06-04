from __future__ import annotations

import streamlit as st

from dashboard.public.results import render_results_page


RESULTS_PAGE = st.Page(
    render_results_page,
    title="View Results",
    icon="📊",
    url_path="results",
)