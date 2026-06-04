from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.scenario_loader import ScenarioBundle

def scenario_label(bundle: ScenarioBundle) -> str:
    return f"{bundle.title} ({bundle.scenario_id}:{bundle.scenario_version})"

def format_session_option(session: dict[str, Any]) -> str:
    return f"{session['session_name']} | {session['scenario_id']} | {session['status']} | {session['session_id']}"

def render_scenario_card(bundle: ScenarioBundle) -> None:
    with st.container(border=True):
        st.subheader(bundle.title)
        st.caption(f"Scenario ID: {bundle.scenario_id} | Domain: {bundle.domain} | Version: {bundle.scenario_version}")
        st.write(bundle.scenario.get("description", "No summary provided."))
        
        tags = bundle.scenario.get("tags", [])
        cols = st.columns(len(tags))
        for i, tag in enumerate(tags):
            cols[i].badge(tag, color="blue")

def render_toasts() -> None:
    """
    Render queued toast messages and remove them from session state.
    """
    messages = st.session_state.pop("_toast_messages", [])

    icon_by_kind = {
        "success": "✅",
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
    }

    for msg in messages:
        kind = msg.get("kind", "info")
        message = msg.get("message", "")
        duration = msg.get("duration", 8)

        st.toast(
            message,
            icon=icon_by_kind.get(kind, "ℹ️"),
            duration=duration,
        )

def push_toast(kind: str, msg: str, *, duration: int | str = 8) -> None:
    """
    Store a toast message that should appear after the next rerun.
    
    kind:
        success, info, warning, error

    duration:
        - "short" = about 4 seconds
        - "long" = about 10 seconds
        - "infinite" = until dismissed
        - int = custom number of seconds
    """
    st.session_state.setdefault("_toast_messages", [])
    st.session_state["_toast_messages"].append(
        {
            "kind": kind,
            "message": msg,
            "duration": duration,
        }
    )

def render_session_filters(
    session_state_key: str,
    available_statuses: list[str] | None = None,
    default_statuses: list[str] | None = None,
    include_mode: bool = True,
    include_weighting: bool = True,
    include_ranking: bool = True,
    include_resubmission: bool = True,
    include_moderator_lock: bool = True,
) -> dict[str, Any]:
    """
    Render a reusable filter container for session searches.
    
    Args:
        session_state_key: Unique key for storing filter state (e.g., 'create_participant_filters')
        available_statuses: List of status options to show in filter (e.g., ['open', 'locked', 'processing_ready', 'completed'])
        default_statuses: Default status filters to apply on first load
        include_mode: Whether to show session mode filter
        include_weighting: Whether to show weighting method filter
        include_ranking: Whether to show ranking method filter
        include_resubmission: Whether to show allow resubmission filter
        include_moderator_lock: Whether to show require moderator lock filter
    
    Returns:
        Dictionary containing filter values for use with list_sessions()
    """
    if available_statuses is None:
        available_statuses = ["open", "locked", "processing_ready", "completed"]
    
    if default_statuses is None:
        default_statuses = available_statuses
    
    # Initialize session state for filters
    if session_state_key not in st.session_state:
        st.session_state[session_state_key] = {
            "statuses": default_statuses,
            "scenario_id": None,
            "mode": None,
            "selected_weighting_method": None,
            "selected_ranking_method": None,
            "allow_resubmission": None,
            "require_moderator_lock": None,
        }
    
    # Ensure stored status values are valid for current available_statuses
    # (handles case where session state from different tab has incompatible values)
    st.session_state[session_state_key]["statuses"] = [
        s for s in st.session_state[session_state_key]["statuses"]
        if s in available_statuses
    ]

    # Filter container
    with st.container(border=True):
        st.subheader("🔍 Filter Sessions")
        
        filters = st.session_state[session_state_key]
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            filters["statuses"] = st.multiselect(
                "Session Status",
                available_statuses,
                default=filters["statuses"],
                key=f"{session_state_key}_status",
            )
        
        if include_mode:
            with col2:
                filters["mode"] = st.selectbox(
                    "Session Mode",
                    [None, "single_stakeholder", "multi_stakeholder"],
                    format_func=lambda x: "All" if x is None else (
                        "Single Stakeholder" if x == "single_stakeholder" else "Multi-Stakeholder"
                    ),
                    key=f"{session_state_key}_mode",
                )
        else:
            with col2:
                filters["mode"] = None
        
        if include_weighting:
            # with col3 if not include_mode else st.columns(1)[0]:
            with col3:
                filters["selected_weighting_method"] = st.selectbox(
                    "Weighting Method",
                    [None, "AHP", "FUZZY_AHP"],
                    format_func=lambda x: "All" if x is None else x,
                    key=f"{session_state_key}_weighting",
                )
        else:
            with col3:
                filters["selected_weighting_method"] = None
        
        col4, col5, col6 = st.columns(3)
        
        if include_ranking:
            with col4:
                filters["selected_ranking_method"] = st.selectbox(
                    "Ranking Method",
                    [None, "TOPSIS", "FUZZY_TOPSIS"],
                    format_func=lambda x: "All" if x is None else x,
                    key=f"{session_state_key}_ranking",
                )
        else:
            filters["selected_ranking_method"] = None
        
        if include_resubmission:
            with col5 if include_ranking else col4:
                allow_resub_option = st.selectbox(
                    "Allow Resubmission",
                    [None, True, False],
                    format_func=lambda x: "All" if x is None else ("Yes" if x else "No"),
                    key=f"{session_state_key}_resubmission",
                )
                filters["allow_resubmission"] = allow_resub_option
        else:
            filters["allow_resubmission"] = None
        
        if include_moderator_lock:
            with col6 if include_ranking and include_resubmission else col5 if include_ranking or include_resubmission else col4:
                require_mod_option = st.selectbox(
                    "Require Moderator Lock",
                    [None, True, False],
                    format_func=lambda x: "All" if x is None else ("Yes" if x else "No"),
                    key=f"{session_state_key}_moderator_lock",
                )
                filters["require_moderator_lock"] = require_mod_option
        else:
            filters["require_moderator_lock"] = None
    
    return filters


def render_criterion_cards(criteria: list[dict[str, Any]]) -> None:
    for c in sorted(criteria, key=lambda x: x.get("display_order", 999)):
        with st.container(border=True):
            criteria_type = c.get("criteria_type", "unknown")
            st.markdown(f"**{c.get('name', c['id'])}**")
            st.caption(f"Criteria Type: {criteria_type.upper()} | Unit: {c.get('unit', 'n/a')}")
            st.write(c.get("description", "No description provided."))


def render_alternative_cards(alternatives: list[dict[str, Any]]) -> None:
    for a in alternatives:
        with st.container(border=True):
            st.markdown(f"**{a.get('name', a['id'])}**")
            st.write(a.get("description", "No description provided."))