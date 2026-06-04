from __future__ import annotations

import streamlit as st

from dashboard.page_registry import RESULTS_PAGE
from dashboard.app_context import get_bundle_for_session_id, get_session
from dashboard.ui_components import (
    render_scenario_card, push_toast,
    render_toasts,
    render_criterion_cards,
    render_alternative_cards
)
from dashboard.preferences import (
    PreferenceValidationError,
    scale_labels,
    validate_preferences,
    transform_linguistic_preferences
)
from dashboard.repositories import (
    list_sessions,
    find_participant_by_access_code,
    get_current_submission,
    submit_preferences_atomic
)

RESULT_HISTORY_KEY = "result_submission_history"

SUBMIT_FLOW_KEYS = [
    "stakeholder_step",
    "stakeholder_info",
    "stakeholder_submission_id",
    "last_submission_result",
]

RESULT_CONTEXT_KEYS = [
    "results_session_id",
    "results_participant_id",
    "results_submission_id",
]


def render_restart_button() -> None:
    """Render a restart button that resets the submission flow to the identification step."""
    if st.button("🔄 Restart from beginning", help="Start a new submission from the beginning"):
        reset_to_new_submission()
        push_toast("info", "Started a new submission.")
        st.rerun()


def render_submit_page() -> None:
    st.title("🗳️ Submit Policy Preferences")
    st.write("Select an open polling session, review the scenario, and submit your criterion preferences.")

    step = st.session_state.get("stakeholder_step", "identify")

    render_toasts()

    with st.container(border=True):
        if step == "identify":
            render_identification_step()
        elif step == "summary":
            render_summary_step()
        elif step == "preferences":
            render_preference_step()
        elif step == "complete":
            render_submission_complete_step()
        else:
            reset_to_new_submission()
            st.rerun()

def render_identification_step() -> None:

    open_sessions = list_sessions(['open'])

    if not open_sessions:
        st.info("No polling sessions are currently open. Wait until the moderator opens a new one.")
        return
    
    session_options = {f"{s['session_name']} | {s['scenario_id']}": s for s in open_sessions}

    selected_label = st.selectbox("Open polling session", list(session_options.keys()))

    session = session_options[selected_label]
    session_id = session["session_id"]
    bundle = get_bundle_for_session_id(session_id)

    if not bundle:
        st.error("The scenario for this session is not available.")
        return
    
    render_scenario_card(bundle)

    stakeholder_groups = {
        g["label"]: g
        for g in bundle.stakeholder_groups
    }

    with st.form("public_identification_form"):
        name = st.text_input("Name or alias")
        alias = st.text_input("Optional alias")
        selected_type_label = st.selectbox(
            "Stakeholder group",
            list(stakeholder_groups.keys()),
        )

        access_code = ""
        if session["require_access_code"]:
            access_code = st.text_input(
                "Participant access code",
                type="password",
                help="Use the code provided by the moderator. This same code can be used later to view results.",
            )
        # else:
        #     st.info("This session does not require an access code.")

        submitted = st.form_submit_button("Continue", type="primary")

    if not submitted:
        return

    if not name.strip():
        st.error("Please enter a name or alias.")
        return

    selected_type = stakeholder_groups[selected_type_label]

    try:
        participant_id = None

        if session["require_access_code"]:
            if not access_code.strip():
                st.error("Please enter your participant access code.")
                return
            
            participant = find_participant_by_access_code(session["session_id"], access_code)
            
            if not participant:
                st.error("Invalid access code.")
                return

            if participant["stakeholder_group_id"] != selected_type["id"]:
                st.error("This access code is not assigned to the selected stakeholder group.")
                return

            existing_submission = get_current_submission(
                session["session_id"],
                participant["participant_id"],
            )

            if existing_submission and not bool(session["allow_resubmission"]):
                st.warning("This participant already submitted preferences for this session.")
                st.info("Use the View Results page to check your submission and wait for moderator results.")
                return

            participant_id = participant["participant_id"]

        st.session_state["stakeholder_info"] = {
            "session_id": session["session_id"],
            "participant_id": participant_id,
            "name": name.strip(),
            "alias": alias.strip() or None,
            "group_id": selected_type["id"],
            "voting_power": float(selected_type.get("default_group_voting_power", 1.0)),
            "require_access_code": bool(session["require_access_code"]),
        }

        st.session_state["stakeholder_step"] = "summary"
        push_toast("success", "Identification complete.")
        st.rerun()

    except Exception as exc:
        st.error(f"Could not identify participant: {exc}")

def render_summary_step() -> None:

    stakeholder_info = st.session_state["stakeholder_info"]

    if not stakeholder_info:
        st.error("Submission context is missing. Please restart the process.")
        if st.button("Restart"):
            reset_to_new_submission()
            st.rerun()
        return

    session_id = stakeholder_info["session_id"]
    bundle = get_bundle_for_session_id(session_id)

    if not bundle:
        st.error("Scenario context is missing. Please restart the stakeholder flow.")
        return

    st.header(bundle.title)

    st.markdown("### Minimal Instructions")
    st.info(
        bundle.ui_config.get(
            "intro_text",
            "You will review the scenario and rate how important each criterion is to your evaluation.",
        )
    )

    st.markdown("### Scenario Description")
    st.write(bundle.scenario.get("description", bundle.scenario.get("summary", "No description provided.")))

    st.markdown("### Policy Question")
    st.write(bundle.scenario.get("policy_question", "No policy question provided."))

    with st.expander("Alternatives", expanded=False):
        render_alternative_cards(bundle.scenario.get("alternatives", []))

    with st.expander("Criteria", expanded=False):
        st.write("Benefit criteria are better when larger. Cost criteria are better when smaller.")
        render_criterion_cards(bundle.criteria)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Back to Identification", width='stretch'):
            clear_result_context()
            st.session_state["stakeholder_step"] = "identify"
            st.rerun()
    with col2:
        if st.button("Start preferences", type="primary", width='stretch'):
            st.session_state["stakeholder_step"] = "preferences"
            st.rerun()

def render_preference_step() -> None:
    render_restart_button()

    stakeholder_info = st.session_state["stakeholder_info"]

    if not stakeholder_info:
        st.error("Submission context is missing. Please restart the process.")
        if st.button("Restart"):
            reset_to_new_submission()
            st.rerun()
        return

    session_id = stakeholder_info["session_id"]
    bundle = get_bundle_for_session_id(session_id)
    session = get_session(session_id)

    if not bundle:
        st.error("Scenario context is missing. Please restart the stakeholder flow.")
        return
    
    if not session or session["status"] != "open":
        st.warning("This session is no longer open for submissions.")
        reset_to_new_submission()
        return

    st.header(bundle.title)

    st.markdown("### Voting Instructions")
    st.info(
        bundle.ui_config.get(
            "voting_instructions",
            "Rate the importance of each criterion using the configured linguistic scale.",
        )
    )

    labels = scale_labels(bundle)

    raw: dict[str, str] = {}
    with st.form("preference_form"):
        st.subheader("Submit Criterion Preferences")
        st.write("Select how important each criterion is for this scenario. All required criteria must be rated.")

        for c in sorted(bundle.criteria, key=lambda x: x.get("display_order", 999)):
            with st.container(border=True):
                st.markdown(f"**{c.get('name', c['id'])}**")
                st.caption(f"Criteria Type: {c.get('criteria_type', 'unknown').upper()} | Unit: {c.get('unit', 'n/a')}")
                st.write(c.get("description", ""))
                raw[c["id"]] = st.select_slider(
                    f"Importance for {c.get('name', c['id'])}",
                    options=labels,
                    value="Medium" if "Medium" in labels else labels[0],
                    key=f"pref_{c['id']}",
                )
        comments = st.text_area("Optional comment for moderator/research notes")
        submitted = st.form_submit_button("Submit preferences", type="primary")
        
    if not submitted:
        return
    
    if not session or session["status"] != "open":
        st.error("This session is no longer open for submissions.")
        return

    try:
        validate_preferences(bundle, raw)

        transformed = transform_linguistic_preferences(bundle, raw)

        if comments.strip():
            transformed["stakeholder_comment"] = comments.strip()
        
        result = submit_preferences_atomic(
            session_id=session_id,
            stakeholder_info=stakeholder_info,
            preference_method=bundle.preference_collection.get(
                "default_method",
                "criterion_linguistic_rating",
            ),
            raw_preferences=raw,
            transformed_preferences=transformed,
            allow_resubmission=bool(session["allow_resubmission"]),
        )

        # Store result context for the completion screen and results page.
        st.session_state["last_submission_result"] = result

        remember_submission_result(
            result,
            label=f"{bundle.title} — {result['submission_id']}",
        )

        # Clear transient form and identification state after successful save.
        for key in ["stakeholder_info", "stakeholder_submission_id"]:
            st.session_state.pop(key, None)

        for key in list(st.session_state.keys()):
            if key.startswith("pref_"):
                st.session_state.pop(key, None)

        st.session_state["stakeholder_step"] = "complete"
        push_toast("success", "Your preferences were submitted successfully.")
        st.rerun()

    except PreferenceValidationError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Could not save submission: {exc}")

def render_submission_complete_step() -> None:
    result = st.session_state.get("last_submission_result")
    history = get_result_history()

    st.success("Your preferences were submitted successfully.")

    if result:
        st.write(f"Submission ID: `{result['submission_id']}`")
        st.caption("This submission has been added to your browser's results list.")

    if history:
        st.info(f"You currently have {len(history)} submission(s) available on the Results page.")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Create another submission", type="secondary", width="stretch"):
            clear_submit_flow_state()
            st.session_state["stakeholder_step"] = "identify"
            push_toast("info", "Ready for a new submission.")
            st.rerun()

    with col2:
        if st.button("View my results", type="primary", width="stretch"):
            clear_submit_flow_state()

            try:
                st.switch_page(RESULTS_PAGE)
            except Exception:
                st.info("Open the View Results page from the sidebar.")

def clear_submit_flow_state() -> None:
    """
    Clear transient public submission flow state.

    This is called after a completed submission and before starting
    another back-to-back submission.
    """
    for key in SUBMIT_FLOW_KEYS:
        st.session_state.pop(key, None)

    # Clear criterion widgets from the previous form.
    for key in list(st.session_state.keys()):
        if key.startswith("pref_"):
            st.session_state.pop(key, None)


def clear_result_context() -> None:
    for key in RESULT_CONTEXT_KEYS:
        st.session_state.pop(key, None)


def reset_to_new_submission() -> None:
    clear_submit_flow_state()
    clear_result_context()
    st.session_state["stakeholder_step"] = "identify"

def get_result_history() -> list[dict]:
    """
    Returns the list of submissions created in this browser session.

    This is browser-session memory only. The database is still the permanent
    source of truth.
    """
    return st.session_state.setdefault(RESULT_HISTORY_KEY, [])


def remember_submission_result(result: dict, label: str | None = None) -> None:
    """
    Store a completed submission in browser session history so the Results page
    can let the user switch between multiple submissions.
    """
    history = get_result_history()

    entry = {
        "session_id": result["session_id"],
        "participant_id": result["participant_id"],
        "submission_id": result["submission_id"],
        "label": label or f"Submission {len(history) + 1}",
    }

    # Prevent duplicates if Streamlit reruns.
    existing_ids = {item["submission_id"] for item in history}
    if entry["submission_id"] not in existing_ids:
        history.append(entry)

    st.session_state[RESULT_HISTORY_KEY] = history

    # Also set this as the currently selected result.
    st.session_state["results_session_id"] = entry["session_id"]
    st.session_state["results_participant_id"] = entry["participant_id"]
    st.session_state["results_submission_id"] = entry["submission_id"]


def clear_submit_flow_state() -> None:
    """
    Clear only transient submit-page state.

    Important:
    This should NOT clear result_submission_history.
    """
    for key in SUBMIT_FLOW_KEYS:
        st.session_state.pop(key, None)

    for key in list(st.session_state.keys()):
        if key.startswith("pref_"):
            st.session_state.pop(key, None)


def clear_current_result_selection() -> None:
    """
    Clears only the selected result, not the full result history.
    """
    for key in RESULT_CONTEXT_KEYS:
        st.session_state.pop(key, None)


def clear_all_result_history() -> None:
    """
    Use only if the user explicitly wants to clear this browser's remembered submissions.
    """
    clear_current_result_selection()
    st.session_state.pop(RESULT_HISTORY_KEY, None)


def reset_to_new_submission() -> None:
    """
    Reset the submit page while preserving result history.
    """
    clear_submit_flow_state()
    st.session_state["stakeholder_step"] = "identify"