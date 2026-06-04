from __future__ import annotations

import json

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from io import BytesIO
from typing import Any
from plotly.subplots import make_subplots

from dashboard.auth import render_admin_login
from dashboard.app_context import get_bundle_for_session_id
from dashboard.ui_components import scenario_label, render_session_filters, push_toast, render_toasts
from dashboard.session_manager import completion_summary
from dashboard.preferences import (
    PreferenceValidationError,
    scale_labels,
    validate_preferences,
    transform_linguistic_preferences,
)
from dashboard.repositories import (
    list_sessions,
    list_participants,
    list_submissions,
    get_session_stakeholder_group_weights,
    update_session_stakeholder_group_weight,
    import_submission_atomic
)

def render_admin_submissions_page() -> None:
    if not render_admin_login():
        return
    
    st.title("📥 Manage Submissions")

    render_toasts()

    tabs = st.tabs(["View Submissions", "Adjust Weights", "Import Submissions"])
    with tabs[0]:
        render_view_submission()
    with tabs[1]:
        render_adjust_weights()
    with tabs[2]:
        render_import_submissions()

def render_view_submission() -> None:
    filters = render_session_filters(
        session_state_key="view_submissions",
        available_statuses=["open", "locked", "preprocessing", "processing_ready", "completed"],
        default_statuses=["open", "locked", "preprocessing", "processing_ready", "completed"]
    )

    sessions = list_sessions(
        statuses=filters["statuses"] if filters["statuses"] else None,
        scenario_id=filters["scenario_id"],
        mode=filters["mode"],
        selected_weighting_method=filters["selected_weighting_method"],
        selected_ranking_method=filters["selected_ranking_method"],
        allow_resubmission=filters["allow_resubmission"],
        require_moderator_lock=filters["require_moderator_lock"],
    )

    if not sessions:
        st.info("No sessions found matching the selected filters.")
        return

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['session_id']}": s
        for s in sessions
    }

    selected = st.selectbox("Session", list(session_options.keys()))
    session = session_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return

    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    submissions = list_submissions(session["session_id"])

    with st.container(border=True):
        if not submissions:
            st.error("There are no submissions for this session.")
            return
        
        st.header(session["session_name"])
        st.caption(f"Mode: {session['mode']} | Session ID: {session['session_id']}")
        st.write(f"Status: `{session['status']}`")

        summary = completion_summary(session["session_id"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Participants", summary["total_participants"])
        c2.metric("Submitted", summary["submitted_participants"])
        c3.metric("Completion", f"{summary['completion_ratio']:.0%}")
        c4.metric("Submitted Power", f"{summary['submitted_normalized_power']:.0%}")

        tabs = st.tabs(["View Submission Details", "View Metrics"])

        with tabs[0]:
            render_view_submission_details(session_id=session["session_id"])
        with tabs[1]:
            render_view_metrics(session_id=session["session_id"])

def _parse_json_column(val):
    """Safely parse a JSON string value."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val

def render_view_submission_details(session_id: str) -> None:
    with st.expander("Participants"):
        participants = list_participants(session_id)
        if not participants:
            st.error("No participants found for this session.")
        else:
            st.dataframe(pd.DataFrame(participants), width="stretch", hide_index=True)
    
    with st.expander("View Submissions"):
        submissions = list_submissions(session_id)
        if not submissions:
            st.error("No submissions found for this session.")
            return
        
        # st.dataframe(pd.DataFrame(submissions), width="stretch")
        df = pd.DataFrame(submissions)

        JSON_COLS = ["raw_preferences_json", "transformed_preferences_json"]
        SUMMARY_COLS = [c for c in df.columns if c not in JSON_COLS]

        st.caption(f"{len(df)} submission(s) — select a row to inspect JSON details.")

        # Summary Table
        event = st.dataframe(
            df[SUMMARY_COLS],
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        selected_rows = event.selection.rows
        if not selected_rows:
            st.info("⬆️ Click a row above to view its preference details.", icon="ℹ️")
            return

        row_idx = selected_rows[0]
        row = df.iloc[row_idx]

        # Detail Panel
        st.divider()
        st.markdown(
            f"**Submission:** `{row.get('submission_id', row_idx)}`  "
            f"&nbsp;|&nbsp; **Participant:** `{row.get('participant_id', '—')}`  "
            f"&nbsp;|&nbsp; **Method:** `{row.get('preference_method', '—')}`  "
            f"&nbsp;|&nbsp; **Status:** `{row.get('validation_status', '—')}`"
        )

        present_json_cols = [c for c in JSON_COLS if c in df.columns]
        if not present_json_cols:
            return

        tabs = st.tabs([col.replace("_", " ").title() for col in present_json_cols])

        for tab, col in zip(tabs, present_json_cols):
            with tab:
                parsed = _parse_json_column(row.get(col))
                if parsed is None:
                    st.caption("No data.")
                elif isinstance(parsed, dict):
                    _render_parsed_json(parsed)
                else:
                    st.json(parsed)

def _render_parsed_json(data: dict) -> None:
    """Render a parsed JSON dict with special handling for known structures."""

    # ── Raw preferences: flat key→label dict ───────────────────────────────────
    if all(isinstance(v, str) for v in data.values()):
        st.dataframe(
            pd.DataFrame(
                [{"Criterion": k, "Rating": v} for k, v in data.items()]
            ),
            width="stretch",
            hide_index=True,
        )
        return

    # ── Transformed preferences: rich nested structure ─────────────────────────
    # Numeric scores summary
    if "numeric_scores" in data and "normalized_direct_weights" in data:
        scores = data["numeric_scores"]
        weights = data["normalized_direct_weights"]
        fuzzy = data.get("fuzzy_linguistic_values", {})

        summary_rows = []
        for criterion in scores:
            fuzzy_vals = fuzzy.get(criterion, [None, None, None])
            summary_rows.append({
                "Criterion": criterion,
                "Score": scores[criterion],
                "Weight": round(weights.get(criterion, 0), 4),
                "Fuzzy (l, m, u)": (
                    f"({fuzzy_vals[0]:.2f}, {fuzzy_vals[1]:.2f}, {fuzzy_vals[2]:.2f})"
                    if len(fuzzy_vals) == 3 else "—"
                ),
            })

        st.markdown("**Criterion Scores & Weights**")
        st.dataframe(
            pd.DataFrame(summary_rows),
            width="stretch",
            hide_index=True,
            column_config={
                "Weight": st.column_config.ProgressColumn(
                    "Weight", min_value=0, max_value=1, format="%.4f"
                ),
            },
        )

    # AHP pairwise matrix
    if "ahp_pairwise_matrix" in data:
        ahp = data["ahp_pairwise_matrix"]
        criteria = ahp.get("criteria_order", [])
        matrix = ahp.get("matrix", [])
        if criteria and matrix:
            st.markdown("**AHP Pairwise Matrix**")
            st.dataframe(
                pd.DataFrame(matrix, index=criteria, columns=criteria).round(4),
                width="stretch",
            )
            if desc := ahp.get("description"):
                st.caption(desc)

    # Remaining top-level keys as raw JSON
    skip = {"numeric_scores", "normalized_direct_weights",
            "fuzzy_linguistic_values", "ahp_pairwise_matrix"}
    leftover = {k: v for k, v in data.items() if k not in skip}
    if leftover:
        with st.expander("Remaining fields (raw JSON)"):
            st.json(leftover)

def render_view_metrics(session_id: str) -> None:
    participants = list_participants(session_id)
    submissions  = list_submissions(session_id)

    if not participants:
        st.info("No participant data available for this session.")
        return

    part_df = pd.DataFrame(participants)
    sub_df  = pd.DataFrame(submissions) if submissions else pd.DataFrame()

    # ── Datetime normalisation ───────────────────────────────────────────────
    for col in ["invited_at", "started_at", "submitted_at"]:
        if col in part_df.columns:
            part_df[col] = pd.to_datetime(part_df[col], utc=True, errors="coerce")

    if not sub_df.empty and "submitted_at" in sub_df.columns:
        sub_df["submitted_at"] = pd.to_datetime(sub_df["submitted_at"], utc=True, errors="coerce")

    submitted_df  = part_df[part_df["status"] == "submitted"]
    pending_df    = part_df[part_df["status"] != "submitted"]
    total         = len(part_df)
    n_submitted   = len(submitted_df)
    n_pending     = total - n_submitted
    completion_pct = n_submitted / total if total else 0

    submitted_power = (
        submitted_df["normalized_voting_power"].sum()
        if "normalized_voting_power" in submitted_df.columns else 0.0
    )

    # ── KPI Row ──────────────────────────────────────────────────────────────
    st.subheader("Overview")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Participants",   total)
    k2.metric("Submitted",      n_submitted)
    k3.metric("Pending",        n_pending)
    k4.metric("Completion",     f"{completion_pct:.0%}")
    k5.metric("Voting Power In",f"{submitted_power:.1%}")

    st.divider()

    # ── Row 1: Stakeholder composition ──────────────────────────────────────
    st.subheader("Stakeholder Composition")
    col_a, col_b = st.columns(2)

    if "stakeholder_group_id" in part_df.columns:
        type_counts = (
            part_df.groupby("stakeholder_group_id")
            .agg(
                Total=("participant_id", "count"),
                Submitted=(
                    "status",
                    lambda s: (s == "submitted").sum(),
                ),
            )
            .reset_index()
            .rename(columns={"stakeholder_group_id": "Stakeholder Group"})
        )

        with col_a:
            st.markdown("**Participants by Stakeholder Group**")
            fig = px.pie(
                type_counts,
                names="Stakeholder Group",
                values="Total",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                height=300,
            )
            st.plotly_chart(fig, width='stretch')

        with col_b:
            if "normalized_voting_power" in part_df.columns:
                st.markdown("**Submitted Voting Power by Stakeholder Group**")
                vp_by_type = (
                    submitted_df.groupby("stakeholder_group_id")["normalized_voting_power"]
                    .sum()
                    .reset_index()
                    .rename(
                        columns={
                            "stakeholder_group_id": "Stakeholder Group",
                            "normalized_voting_power": "Voting Power",
                        }
                    )
                )
                fig2 = px.pie(
                    vp_by_type,
                    names="Stakeholder Group",
                    values="Voting Power",
                    hole=0.45,
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig2.update_traces(
                    textposition="inside",
                    textinfo="percent+label",
                    hovertemplate="%{label}: %{value:.3f} (%{percent})<extra></extra>",
                )
                fig2.update_layout(
                    margin=dict(t=10, b=10, l=10, r=10),
                    showlegend=True,
                    height=300,
                )
                st.plotly_chart(fig2, width='stretch')

    st.divider()

    # ── Row 2: Submission status per stakeholder group ────────────────────────
    st.subheader("Submission Status by Stakeholder Group")
    if "stakeholder_group_id" in part_df.columns:
        status_by_type = (
            part_df.groupby(["stakeholder_group_id", "status"])
            .size()
            .reset_index(name="Count")
            .rename(columns={"stakeholder_group_id": "Stakeholder Group", "status": "Status"})
        )
        fig3 = px.bar(
            status_by_type,
            x="Stakeholder Group",
            y="Count",
            color="Status",
            barmode="stack",
            text_auto=True,
            color_discrete_map={
                "submitted": "#2ecc71",
                "started":   "#f39c12",
                "invited":   "#95a5a6",
            },
        )
        fig3.update_layout(
            margin=dict(t=10, b=10, l=10, r=10),
            height=300,
            yaxis_title="Participants",
            legend_title="Status",
        )
        st.plotly_chart(fig3, width='stretch')

    st.divider()

    # ── Row 3: Submission timeline ───────────────────────────────────────────
    st.subheader("Submission Timeline")
    if not sub_df.empty and "submitted_at" in sub_df.columns:
        # Use only current submissions for timeline
        timeline_df = (
            sub_df[sub_df.get("is_current", pd.Series(True, index=sub_df.index)).astype(bool)]
            if "is_current" in sub_df.columns
            else sub_df
        ).copy()

        timeline_df = timeline_df.dropna(subset=["submitted_at"]).sort_values("submitted_at")

        if not timeline_df.empty:
            timeline_df["Cumulative Submissions"] = range(1, len(timeline_df) + 1)
            timeline_df["submitted_at_local"] = timeline_df["submitted_at"].dt.tz_convert(None)

            fig4 = go.Figure()
            fig4.add_trace(
                go.Scatter(
                    x=timeline_df["submitted_at_local"],
                    y=timeline_df["Cumulative Submissions"],
                    mode="lines+markers",
                    fill="tozeroy",
                    line=dict(color="#3498db", width=2),
                    marker=dict(size=6),
                    hovertemplate=(
                        "%{x|%b %d %H:%M}<br>Cumulative: %{y}<extra></extra>"
                    ),
                )
            )

            # Reference line: total participants
            fig4.add_hline(
                y=total,
                line_dash="dash",
                line_color="#e74c3c",
                annotation_text=f"Total participants ({total})",
                annotation_position="top left",
            )

            fig4.update_layout(
                xaxis_title="Time (UTC)",
                yaxis_title="Cumulative Submissions",
                margin=dict(t=20, b=10, l=10, r=10),
                height=320,
            )
            st.plotly_chart(fig4, width='stretch')
        else:
            st.info("No submission timestamps available for timeline.")
    else:
        st.info("No submission data available for timeline.")

    st.divider()

    # ── Row 4: Response-time distribution & voting-power spread ─────────────
    col_c, col_d = st.columns(2)

    with col_c:
        st.subheader("Time to Submit")
        if (
            "invited_at" in submitted_df.columns
            and "submitted_at" in submitted_df.columns
        ):
            rt_df = submitted_df.dropna(subset=["invited_at", "submitted_at"]).copy()
            rt_df["minutes_to_submit"] = (
                rt_df["submitted_at"] - rt_df["invited_at"]
            ).dt.total_seconds() / 60

            rt_df = rt_df[rt_df["minutes_to_submit"] >= 0]

            if not rt_df.empty:
                fig5 = px.histogram(
                    rt_df,
                    x="minutes_to_submit",
                    nbins=20,
                    labels={"minutes_to_submit": "Minutes from Invite to Submit"},
                    color_discrete_sequence=["#9b59b6"],
                )
                fig5.update_layout(
                    yaxis_title="Participants",
                    margin=dict(t=10, b=10, l=10, r=10),
                    height=280,
                )
                st.plotly_chart(fig5, width='stretch')

                median_mins = rt_df["minutes_to_submit"].median()
                mean_mins   = rt_df["minutes_to_submit"].mean()
                m1, m2 = st.columns(2)
                m1.metric("Median (min)", f"{median_mins:.1f}")
                m2.metric("Mean (min)",   f"{mean_mins:.1f}")
            else:
                st.info("Not enough timing data to plot.")
        else:
            st.info("Invite/submit timestamps not available.")

    with col_d:
        st.subheader("Voting Power Distribution")
        if "normalized_voting_power" in part_df.columns and "display_name" in part_df.columns:
            vp_df = part_df[["display_name", "stakeholder_group_id", "normalized_voting_power", "status"]].copy()
            vp_df = vp_df.sort_values("normalized_voting_power", ascending=False)

            fig6 = px.bar(
                vp_df,
                x="display_name",
                y="normalized_voting_power",
                color="stakeholder_group_id",
                color_discrete_map={
                    "submitted": "#2ecc71",
                    "started":   "#f39c12",
                    "invited":   "#95a5a6",
                },
                labels={
                    "display_name": "Participant",
                    "normalized_voting_power": "Normalised Voting Power",
                    "status": "Status",
                },
                hover_data={"stakeholder_group_id": True},
            )
            fig6.update_layout(
                xaxis_tickangle=-45,
                margin=dict(t=10, b=60, l=10, r=10),
                height=280,
                showlegend=True,
            )
            st.plotly_chart(fig6, width='stretch')
        else:
            st.info("Voting power data not available.")

    st.divider()

    # ── Row 5: Resubmissions ─────────────────────────────────────────────────
    st.subheader("Resubmission Activity")
    if not sub_df.empty and "submission_version" in sub_df.columns:
        max_version_df = (
            sub_df.groupby("participant_id")["submission_version"]
            .max()
            .reset_index()
            .rename(columns={"submission_version": "Max Version"})
        )
        resubmitted_count = int((max_version_df["Max Version"] > 1).sum())
        first_time_count  = int((max_version_df["Max Version"] == 1).sum())

        r1, r2, r3 = st.columns(3)
        r1.metric("First-Time Submitters",  first_time_count)
        r2.metric("Resubmitted ≥ 1×",       resubmitted_count)
        r3.metric("Max Versions Seen",       int(max_version_df["Max Version"].max()))

        if resubmitted_count > 0:
            version_counts = (
                max_version_df["Max Version"]
                .value_counts()
                .sort_index()
                .reset_index()
                .rename(columns={"Max Version": "Submission Version", "count": "Participants"})
            )
            fig7 = px.bar(
                version_counts,
                x="Submission Version",
                y="Participants",
                text_auto=True,
                color_discrete_sequence=["#1abc9c"],
                labels={"Submission Version": "Highest Version Submitted"},
            )
            fig7.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                height=240,
                xaxis=dict(tickmode="linear", dtick=1),
            )
            st.plotly_chart(fig7, width='stretch')
    else:
        st.info("No submission version data available.")

    st.divider()

    # ── Row 6: Raw data expanders ────────────────────────────────────────────
    with st.expander("Raw participant data"):
        st.dataframe(part_df, width='stretch', hide_index=True)

    if not sub_df.empty:
        with st.expander("Raw submission data"):
            JSON_COLS = ["raw_preferences_json", "transformed_preferences_json"]
            display_cols = [c for c in sub_df.columns if c not in JSON_COLS]
            st.dataframe(sub_df[display_cols], width='stretch', hide_index=True)

def render_adjust_weights() -> None:
    filters = render_session_filters(
        session_state_key="adjust_weights",
        available_statuses=["open", "locked"],
        default_statuses=["open", "locked"],
    )

    sessions = list_sessions(
        statuses=filters["statuses"] if filters["statuses"] else None,
        scenario_id=filters["scenario_id"],
        mode=filters["mode"],
        selected_weighting_method=filters["selected_weighting_method"],
        selected_ranking_method=filters["selected_ranking_method"],
        allow_resubmission=filters["allow_resubmission"],
        require_moderator_lock=filters["require_moderator_lock"],
    )

    if not sessions:
        st.info("No sessions found matching the selected filters.")
        return

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['session_id']}": s
        for s in sessions
    }

    selected = st.selectbox("Session", list(session_options.keys()), key="selected_adjust_weights")
    session = session_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return

    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    st.header("Adjust Stakeholder-Type Voting Weights")

    participants = list_participants(session["session_id"])
    submissions = list_submissions(session["session_id"])
    current_group_weights = get_session_stakeholder_group_weights(session["session_id"])

    participant_counts_by_type: dict[str, int] = {}
    submitted_counts_by_type: dict[str, int] = {}

    for participant in participants:
        stakeholder_group_id = participant["stakeholder_group_id"]
        participant_counts_by_type[stakeholder_group_id] = (
            participant_counts_by_type.get(stakeholder_group_id, 0) + 1
        )

    for submission in submissions:
        stakeholder_group_id = submission.get("stakeholder_group_id")
        submitted_counts_by_type[stakeholder_group_id] = (
            submitted_counts_by_type.get(stakeholder_group_id, 0) + 1
        )

    rows = []
    for group in bundle.stakeholder_groups:
        stakeholder_group_id = group["id"]
        scenario_default = float(group.get("default_group_voting_power", 1.0))

        current_weight = current_group_weights.get(
            stakeholder_group_id,
            scenario_default,
        )

        rows.append(
            {
                "Stakeholder Group ID": stakeholder_group_id,
                "Label": group.get("label", stakeholder_group_id),
                "Description": group.get("description", ""),
                "Scenario Default": scenario_default,
                "Session Weight": float(current_weight),
                "Participants": participant_counts_by_type.get(stakeholder_group_id, 0),
                "Submitted": submitted_counts_by_type.get(stakeholder_group_id, 0),
            }
        )

    if not rows:
        st.warning("This scenario does not define stakeholder groups.")
        return

    st.caption(
        "Edit only the `Session Weight` column. Saved values will be applied to all "
        "current participants in that stakeholder group and used by future import logic."
    )

    edited_df = st.data_editor(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        disabled=[
            "Stakeholder Group ID",
            "Label",
            "Description",
            "Scenario Default",
            "Participants",
            "Submitted",
        ],
        column_config={
            "Session Weight": st.column_config.NumberColumn(
                "Session Weight",
                min_value=0.0,
                step=0.05,
                format="%.4f",
                help="Raw stakeholder-type voting power for this session.",
            ),
            "Scenario Default": st.column_config.NumberColumn(
                "Scenario Default",
                format="%.4f",
            ),
        },
        key=f"stakeholder_group_weight_editor_{session['session_id']}",
    )

    total_weight = float(edited_df["Session Weight"].sum())

    col1, col2, col3 = st.columns(3)
    col1.metric("Stakeholder Groups", len(edited_df))
    col2.metric("Total Raw Weight", round(total_weight, 4))
    col3.metric("Participants", len(participants))

    if total_weight <= 0:
        st.error("Total stakeholder-type weight must be greater than 0 before saving.")
        return

    if abs(total_weight - 1.0) > 0.001:
        st.warning(
            "The stakeholder-type weights do not sum to 1. This is allowed because "
            "participant voting power is normalized later, but for readability you may "
            "prefer weights that sum to 1."
        )

    reason = st.text_input(
        "Reason for adjustment",
        value="Moderator adjusted stakeholder-type voting weights for this session.",
        key=f"adjust_weight_reason_{session['session_id']}",
    )

    if st.button("Save Stakeholder-Type Weights", type="primary"):
        try:
            for _, row in edited_df.iterrows():
                update_session_stakeholder_group_weight(
                    session_id=session["session_id"],
                    stakeholder_group_id=str(row["Stakeholder Group ID"]),
                    new_group_voting_power=float(row["Session Weight"]),
                    reason=reason.strip() or None,
                    changed_by="moderator",
                )
            push_toast("success",
                       "Stakeholder-type weights saved. Existing participants in each group "
                        "were updated and normalized voting power was recalculated.")
            st.rerun()

        except Exception as exc:
            st.error(f"Could not save stakeholder-type weights: {exc}")

def build_submission_import_template(bundle) -> pd.DataFrame:
    stakeholder_group_id = (
        bundle.stakeholder_groups[0]["id"]
        if bundle.stakeholder_groups
        else "example_stakeholder_group"
    )

    labels = scale_labels(bundle)
    default_rating = "Medium" if "Medium" in labels else labels[0]

    row_1 = {
        "display_name": "Test Stakeholder 1",
        "alias": "test_1",
        "stakeholder_group_id": stakeholder_group_id,
        "external_ref": "import_test_001",
        "comment": "Example imported submission.",
    }

    row_2 = {
        "display_name": "Test Stakeholder 2",
        "alias": "test_2",
        "stakeholder_group_id": stakeholder_group_id,
        "external_ref": "import_test_002",
        "comment": "Second example imported submission.",
    }

    for criterion in bundle.criteria:
        criterion_id = criterion["id"]
        row_1[criterion_id] = default_rating
        row_2[criterion_id] = "High" if "High" in labels else default_rating

    return pd.DataFrame([row_1, row_2])


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="submissions")
    return output.getvalue()


def read_submission_import_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)

    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded_file)

    raise ValueError("Unsupported file type. Upload a CSV or Excel file.")


def validate_import_dataframe(
    *,
    df: pd.DataFrame,
    bundle,
    session: dict,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required_meta_cols = ["display_name", "stakeholder_group_id"]
    criterion_ids = [c["id"] for c in bundle.criteria if c.get("required", True)]
    allowed_labels = set(scale_labels(bundle))
    stakeholder_group_ids = {g["id"] for g in bundle.stakeholder_groups}

    validation_rows: list[dict[str, Any]] = []
    prepared_rows: list[dict[str, Any]] = []

    missing_meta = [col for col in required_meta_cols if col not in df.columns]
    missing_criteria = [criterion_id for criterion_id in criterion_ids if criterion_id not in df.columns]

    if missing_meta or missing_criteria:
        for idx in range(len(df)):
            validation_rows.append(
                {
                    "Row": idx + 2,
                    "Display Name": "",
                    "Stakeholder Group": "",
                    "Valid": False,
                    "Error": (
                        f"Missing columns. Metadata: {missing_meta}; "
                        f"Criteria: {missing_criteria}"
                    ),
                }
            )
        return validation_rows, prepared_rows

    for idx, row in df.iterrows():
        row_number = idx + 2
        errors = []

        display_name = str(row.get("display_name", "")).strip()
        alias = str(row.get("alias", "")).strip() if "alias" in df.columns else None
        external_ref = (
            str(row.get("external_ref", "")).strip()
            if "external_ref" in df.columns
            else None
        )
        stakeholder_group_id = str(row.get("stakeholder_group_id", "")).strip()

        if not display_name:
            errors.append("display_name is required")

        if stakeholder_group_id not in stakeholder_group_ids:
            errors.append(
                f"stakeholder_group_id must be one of: {', '.join(sorted(stakeholder_group_ids))}"
            )

        raw_preferences = {}

        for criterion_id in criterion_ids:
            value = row.get(criterion_id)

            if pd.isna(value):
                errors.append(f"{criterion_id} is missing")
                continue

            rating = str(value).strip()
            raw_preferences[criterion_id] = rating

            if rating not in allowed_labels:
                errors.append(
                    f"{criterion_id} has invalid rating '{rating}'. "
                    f"Allowed: {', '.join(sorted(allowed_labels))}"
                )

        transformed_preferences = None

        if not errors:
            try:
                validate_preferences(bundle, raw_preferences)
                transformed_preferences = transform_linguistic_preferences(
                    bundle,
                    raw_preferences,
                )

                comment = (
                    str(row.get("comment", "")).strip()
                    if "comment" in df.columns and not pd.isna(row.get("comment"))
                    else ""
                )

                if comment:
                    transformed_preferences["stakeholder_comment"] = comment

            except PreferenceValidationError as exc:
                errors.append(str(exc))
            except Exception as exc:
                errors.append(f"Could not transform preferences: {exc}")

        valid = not errors

        validation_rows.append(
            {
                "Row": row_number,
                "Display Name": display_name,
                "Stakeholder Group": stakeholder_group_id,
                "Valid": valid,
                "Error": "; ".join(errors),
            }
        )

        if valid:
            group_defaults = {
                g["id"]: float(g.get("default_group_voting_power", 1.0))
                for g in bundle.stakeholder_groups
            }

            prepared_rows.append(
                {
                    "row_number": row_number,
                    "display_name": display_name,
                    "alias": alias or None,
                    "external_ref": external_ref or None,
                    "stakeholder_group_id": stakeholder_group_id,
                    "raw_preferences": raw_preferences,
                    "transformed_preferences": transformed_preferences,
                    "default_voting_power": group_defaults[stakeholder_group_id],
                }
            )

    return validation_rows, prepared_rows

def reset_import_uploader(session_id: str) -> None:
    """
    Reset the Streamlit file uploader by changing its widget key.

    st.file_uploader cannot be cleared directly by assigning None.
    The reliable pattern is to change the widget key and rerun.
    """
    key_name = f"submission_import_uploader_version_{session_id}"
    st.session_state[key_name] = st.session_state.get(key_name, 0) + 1

def render_import_submissions() -> None:
    filters = render_session_filters(
        session_state_key="import_submissions",
        available_statuses=["open", "locked"],
        default_statuses=["open"]
    )

    sessions = list_sessions(
        statuses=filters["statuses"] if filters["statuses"] else None,
        scenario_id=filters["scenario_id"],
        mode=filters["mode"],
        selected_weighting_method=filters["selected_weighting_method"],
        selected_ranking_method=filters["selected_ranking_method"],
        allow_resubmission=filters["allow_resubmission"],
        require_moderator_lock=filters["require_moderator_lock"],
    )

    if not sessions:
        st.info("No sessions found matching the selected filters.")
        return

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['session_id']}": s
        for s in sessions
    }

    selected = st.selectbox("Session", list(session_options.keys()), key="selected_import_submission")
    session = session_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return

    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    st.header("Import Submissions")
    st.write(
        "Upload a CSV or Excel file where each row represents one stakeholder submission. "
        "The file must include a stakeholder group and one preference rating per criterion."
    )

    if session["status"] == "locked":
        st.warning(
            "This session is locked. Importing into a locked session is useful for testing, "
            "but it bypasses the normal public submission timing rules."
        )

    if session["mode"] == "single_stakeholder":
        st.warning(
            "This is a single-stakeholder session. Importing multiple rows will fail. "
            "Use a multi-stakeholder session for mass testing."
        )

    template_df = build_submission_import_template(bundle)

    st.markdown("#### Download Template")
    csv_bytes = template_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV Template",
        data=csv_bytes,
        file_name=f"{bundle.scenario_id}_submission_import_template.csv",
        mime="text/csv",
    )

    excel_bytes = dataframe_to_excel_bytes(template_df)
    st.download_button(
        "Download Excel Template",
        data=excel_bytes,
        file_name=f"{bundle.scenario_id}_submission_import_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()

    uploader_version = st.session_state.get(
        f"submission_import_uploader_version_{session['session_id']}",
        0,
    )

    uploaded_file = st.file_uploader(
        "Upload completed CSV or Excel file",
        type=["csv", "xlsx", "xls"],
        key=f"submission_import_file_{session['session_id']}_{uploader_version}",
    )
    if uploaded_file is None:
        return
    
    try:
        import_df = read_submission_import_file(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read uploaded file: {exc}")
        return

    if import_df.empty:
        st.error("The uploaded file has no rows.")
        return

    st.markdown("#### Preview Uploaded Data")
    st.dataframe(import_df.head(20), width="stretch", hide_index=True)
    st.caption(f"Uploaded rows: {len(import_df)}")

    validation_rows, prepared_rows = validate_import_dataframe(
        df=import_df,
        bundle=bundle,
        session=session,
    )

    validation_df = pd.DataFrame(validation_rows)

    st.markdown("#### Validation Results")
    st.dataframe(
        validation_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Valid": st.column_config.CheckboxColumn("Valid"),
        },
    )

    error_count = len([row for row in validation_rows if not row["Valid"]])

    if error_count:
        st.error(f"{error_count} row(s) have validation errors. Fix the file and upload again.")
        return

    st.success("All rows passed validation.")

    generate_access_codes = st.checkbox(
        "Generate participant access codes for imported participants",
        value=False,
        help=(
            "Useful if imported test participants need to access the public Results page later. "
            "For pure mass testing, this can stay off."
        ),
    )

    allow_locked_import = False
    if session["status"] == "locked":
        allow_locked_import = st.checkbox(
            "Allow import into locked session for testing",
            value=False,
        )

    if session["status"] == "locked" and not allow_locked_import:
        st.info("Enable locked-session import above if this is intentional.")
        return

    already_imported_this_upload = st.session_state.get(
        f"last_import_completed_{session['session_id']}",
        False,
    )

    if already_imported_this_upload:
        st.warning(
            "This uploaded file has already been imported. Clear the uploaded file before importing again."
        )
        return

    if st.button("Import Valid Submissions", type="primary"):
        imported = []
        failed = []

        progress = st.progress(0)
        total = len(prepared_rows)

        for idx, prepared in enumerate(prepared_rows, start=1):
            try:
                result = import_submission_atomic(
                    session_id=session["session_id"],
                    stakeholder_group_id=prepared["stakeholder_group_id"],
                    display_name=prepared["display_name"],
                    alias=prepared.get("alias"),
                    external_ref=prepared.get("external_ref"),
                    default_voting_power=prepared["default_voting_power"],
                    raw_preferences=prepared["raw_preferences"],
                    transformed_preferences=prepared["transformed_preferences"],
                    preference_method=bundle.preference_collection.get(
                        "default_method",
                        "criterion_linguistic_rating",
                    ),
                    generate_access_code=generate_access_codes,
                    allow_locked_session=allow_locked_import,
                    source="bulk_import",
                )
                imported.append(result)

            except Exception as exc:
                failed.append(
                    {
                        "row_number": prepared["row_number"],
                        "display_name": prepared["display_name"],
                        "error": str(exc),
                    }
                )

            progress.progress(idx / total)

        if imported:
            st.success(f"Imported {len(imported)} submission(s).")

            imported_df = pd.DataFrame(imported)
            st.dataframe(imported_df, width="stretch", hide_index=True)

            if generate_access_codes and "access_code" in imported_df.columns:
                st.warning(
                    "Access codes are shown only now. Download or copy them before clearing the upload."
                )

                st.download_button(
                    "Download Imported Participant Access Codes",
                    data=imported_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"{session['session_id']}_imported_access_codes.csv",
                    mime="text/csv",
                )

            st.session_state[f"last_import_completed_{session['session_id']}"] = True

            if st.session_state.get(f"last_import_completed_{session['session_id']}", False):
                if st.button("Clear uploaded file and start another import", type="primary"):
                    st.session_state.pop(f"last_import_completed_{session['session_id']}", None)
                    reset_import_uploader(session["session_id"])
                    st.rerun()

        if failed:
            st.error(f"{len(failed)} row(s) failed during import.")
            st.dataframe(pd.DataFrame(failed), width="stretch", hide_index=True)