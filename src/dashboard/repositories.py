from __future__ import annotations

import json
import sqlite3

from typing import Any
from collections import defaultdict

from dashboard.db import get_connection
from dashboard.scenario_loader import ScenarioBundle
from dashboard.utils.time import utc_now_iso
from dashboard.utils.ids import generate_access_code as generate_access_code_value, hash_access_code, new_id, hash_text
from dashboard.access_codes import generate_participant_access_code


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RepositoryError(RuntimeError):
    pass


def save_scenario_snapshot(bundle: ScenarioBundle) -> None:
    """Save scenario configuration snapshot for audit trail."""
    now = utc_now_iso()
    snapshot = {
        "scenario": bundle.scenario,
        "criteria": bundle.criteria,
        "data_sources": bundle.data_sources,
        "preprocessing": bundle.preprocessing,
        "session_rules": bundle.session_rules,
        "ui_config": bundle.ui_config,
    }

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO scenarios (
                scenario_id, scenario_version, title, domain, folder_path, config_hash,
                config_snapshot_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(scenario_id, scenario_version) DO UPDATE SET
                title=excluded.title,
                domain=excluded.domain,
                folder_path=excluded.folder_path,
                config_hash=excluded.config_hash,
                config_snapshot_json=excluded.config_snapshot_json,
                updated_at=excluded.updated_at
            """,
            (
                bundle.scenario_id,
                bundle.scenario_version,
                bundle.title,
                bundle.domain,
                str(bundle.folder),
                bundle.config_hash,
                json.dumps(snapshot, indent=2),
                now,
                now,
            )
        )


def initialize_session_stakeholder_groups(
    session_id: str,
    bundle: ScenarioBundle,
) -> None:
    """
    Initialize stakeholder group configuration for a new session.
    
    Copies default group voting powers from the scenario bundle into the
    session_stakeholder_groups table. Processing later uses the database
    table as the source of truth, so moderator edits are reflected.
    
    Args:
        session_id: Session to initialize
        bundle: Scenario bundle containing stakeholder groups
    
    Raises:
        RepositoryError: If session doesn't exist or no groups in bundle
    """
    now = utc_now_iso()
    
    with get_connection() as conn:
        # Verify session exists
        session = conn.execute(
            "SELECT session_id FROM polling_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        
        if not session:
            raise RepositoryError(f"Unknown session: {session_id}")
        
        if not bundle.stakeholder_groups:
            raise RepositoryError("Scenario has no stakeholder groups defined")
        
        # Collect raw group weights
        raw_weights = []
        for group in bundle.stakeholder_groups:
            group_id = group["id"]
            default_power = float(group.get("default_group_voting_power", 1.0))
            raw_weights.append(default_power)
            
            conn.execute(
                """
                INSERT INTO session_stakeholder_groups (
                    session_id, stakeholder_group_id, display_name,
                    default_group_voting_power, current_group_voting_power,
                    normalized_group_voting_power, is_active,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0.0, 1, ?, ?)
                """,
                (
                    session_id,
                    group_id,
                    group.get("label", group_id),
                    default_power,
                    default_power,
                    now,
                    now,
                ),
            )
        
        # Normalize weights immediately
        _normalize_session_stakeholder_group_weights_in_conn(conn, session_id)


def _normalize_session_stakeholder_group_weights_in_conn(
    conn: sqlite3.Connection,
    session_id: str,
) -> None:
    """
    Recalculate normalized group voting powers within an open transaction.
    
    Sums current group voting powers for active groups, then stores
    normalized value (power / total) for each active group.
    """
    rows = conn.execute(
        """
        SELECT stakeholder_group_id, current_group_voting_power
        FROM session_stakeholder_groups
        WHERE session_id = ? AND is_active = 1
        """,
        (session_id,),
    ).fetchall()
    
    if not rows:
        raise RepositoryError(
            f"No active stakeholder groups found for session {session_id}. "
            "Cannot normalize weights."
        )
    
    total = sum(row["current_group_voting_power"] for row in rows)
    
    if total <= 0:
        raise RepositoryError(
            f"Active stakeholder group weights sum to zero for session {session_id}. "
            "Cannot normalize."
        )
    
    now = utc_now_iso()
    
    for row in rows:
        group_id = row["stakeholder_group_id"]
        current_power = row["current_group_voting_power"]
        normalized = current_power / total
        
        conn.execute(
            """
            UPDATE session_stakeholder_groups
            SET normalized_group_voting_power = ?, updated_at = ?
            WHERE session_id = ? AND stakeholder_group_id = ?
            """,
            (normalized, now, session_id, group_id),
        )


def normalize_session_stakeholder_group_weights(session_id: str) -> None:
    """
    Recalculate normalized group voting powers for a session.
    
    Call this after updating any group voting power to refresh all normalized weights.
    
    Raises:
        RepositoryError: If no active groups or weights sum to zero
    """
    with get_connection() as conn:
        _normalize_session_stakeholder_group_weights_in_conn(conn, session_id)


def get_session_stakeholder_group_weights(session_id: str) -> dict[str, float]:
    """
    Return normalized stakeholder group voting weights for a session.
    
    Reads from session_stakeholder_groups table (database source of truth),
    not from scenario bundle. Returns only active groups' normalized weights.
    
    Args:
        session_id: Session to fetch weights for
    
    Returns:
        Dict mapping stakeholder_group_id → normalized weight (0.0-1.0)
    
    Raises:
        RepositoryError: If no active groups found or weights sum to zero
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT stakeholder_group_id, normalized_group_voting_power
            FROM session_stakeholder_groups
            WHERE session_id = ? AND is_active = 1
            """,
            (session_id,),
        ).fetchall()
    
    if not rows:
        raise RepositoryError(
            f"No active stakeholder groups found for session {session_id}. "
            "Session may not be initialized."
        )
    
    result = {}
    for row in rows:
        group_id = row["stakeholder_group_id"]
        normalized = row["normalized_group_voting_power"]
        
        if normalized <= 0:
            raise RepositoryError(
                f"Stakeholder group {group_id} has invalid normalized weight {normalized}. "
                "Call normalize_session_stakeholder_group_weights() to recalculate."
            )
        
        result[group_id] = normalized
    
    return result


def list_session_stakeholder_groups(session_id: str) -> list[dict[str, Any]]:
    """
    List all stakeholder groups configured for a session.
    
    Args:
        session_id: Session to query
    
    Returns:
        List of stakeholder group dicts with id, display_name, weights, status
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                stakeholder_group_id,
                display_name,
                default_group_voting_power,
                current_group_voting_power,
                normalized_group_voting_power,
                is_active,
                created_at,
                updated_at
            FROM session_stakeholder_groups
            WHERE session_id = ?
            ORDER BY stakeholder_group_id
            """,
            (session_id,),
        ).fetchall()
    
    return [dict(row) for row in rows]


def update_session_stakeholder_group_weight(
    *,
    session_id: str,
    stakeholder_group_id: str,
    new_group_voting_power: float,
    reason: str | None = None,
    changed_by: str | None = None,
) -> None:
    """
    Update stakeholder group voting power for a session.
    
    Updates the session_stakeholder_groups table and creates an audit
    history entry. Does not modify participant-level voting power
    (participant voting power is preference evidence, not institutional influence).
    
    After updating, all active group normalized weights are recalculated.
    
    Args:
        session_id: Session containing the group
        stakeholder_group_id: Group to update
        new_group_voting_power: New raw voting power value (will be normalized)
        reason: Optional reason for the change (for audit)
        changed_by: Optional actor making the change (for audit)
    
    Raises:
        RepositoryError: If session/group doesn't exist, weight is negative,
                        or weights would sum to zero
    """
    if new_group_voting_power < 0:
        raise RepositoryError("Group voting power cannot be negative.")
    
    now = utc_now_iso()
    
    with get_connection() as conn:
        # Verify session and group exist
        session = conn.execute(
            "SELECT session_id FROM polling_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        
        if not session:
            raise RepositoryError(f"Unknown session: {session_id}")
        
        group_row = conn.execute(
            """
            SELECT current_group_voting_power
            FROM session_stakeholder_groups
            WHERE session_id = ? AND stakeholder_group_id = ?
            """,
            (session_id, stakeholder_group_id),
        ).fetchone()
        
        if not group_row:
            raise RepositoryError(
                f"Unknown stakeholder group {stakeholder_group_id} in session {session_id}"
            )
        
        old_power = group_row["current_group_voting_power"]
        
        # Create audit history entry
        conn.execute(
            """
            INSERT INTO stakeholder_group_weight_history (
                history_id, session_id, stakeholder_group_id,
                old_group_voting_power, new_group_voting_power,
                reason, changed_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("wh"),
                session_id,
                stakeholder_group_id,
                old_power,
                new_group_voting_power,
                reason,
                changed_by,
                now,
            ),
        )
        
        # Update group weight
        conn.execute(
            """
            UPDATE session_stakeholder_groups
            SET current_group_voting_power = ?, updated_at = ?
            WHERE session_id = ? AND stakeholder_group_id = ?
            """,
            (new_group_voting_power, now, session_id, stakeholder_group_id),
        )
        
        # Recalculate all normalized weights
        _normalize_session_stakeholder_group_weights_in_conn(conn, session_id)


def save_stakeholder_group_aggregation_results(
    *,
    result_id: str,
    session_id: str,
    export_id: str | None,
    stakeholder_group_id: str | None,
    aggregation_level: str,
    criteria_order: list[str],
    pairwise_matrix: list[list[float]],
    weights: dict[str, float] | None = None,
    consistency_ratio: float | None = None,
    is_consistent: bool | None = None,
    submission_count: int | None = None,
) -> None:
    """
    Save stakeholder group aggregation result (matrices and weights).
    
    Called after group AHP computation to persist per-group and final
    aggregate matrices for later dashboard review and audit.
    
    Args:
        result_id: Unique result record ID
        session_id: Session being processed
        export_id: Optional export record this result belongs to
        stakeholder_group_id: Group being aggregated (None for final aggregate)
        aggregation_level: 'stakeholder_group' or 'final_group'
        criteria_order: List of criteria in matrix
        pairwise_matrix: 2D pairwise comparison matrix
        weights: Optional per-criterion weights dict
        consistency_ratio: Optional AHP consistency ratio
        is_consistent: Optional boolean indicating if matrix is consistent
        submission_count: Optional count of submissions aggregated
    """
    now = utc_now_iso()
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO stakeholder_group_aggregation_results (
                result_id, session_id, export_id, stakeholder_group_id,
                aggregation_level, criteria_order_json, pairwise_matrix_json,
                weights_json, consistency_ratio, is_consistent, submission_count,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                session_id,
                export_id,
                stakeholder_group_id,
                aggregation_level,
                json.dumps(criteria_order),
                json.dumps(pairwise_matrix),
                json.dumps(weights) if weights else None,
                consistency_ratio,
                int(is_consistent) if is_consistent is not None else None,
                submission_count,
                now,
            ),
        )


def list_stakeholder_group_aggregation_results(
    session_id: str,
    export_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    List stakeholder group aggregation results for a session.
    
    Args:
        session_id: Session to query
        export_id: Optional filter by export record
    
    Returns:
        List of aggregation result dicts
    """
    with get_connection() as conn:
        if export_id:
            rows = conn.execute(
                """
                SELECT *
                FROM stakeholder_group_aggregation_results
                WHERE session_id = ? AND export_id = ?
                ORDER BY aggregation_level DESC, created_at DESC
                """,
                (session_id, export_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM stakeholder_group_aggregation_results
                WHERE session_id = ?
                ORDER BY aggregation_level DESC, created_at DESC
                """,
                (session_id,),
            ).fetchall()
    
    return [dict(row) for row in rows]


def create_session(
    bundle: ScenarioBundle,
    session_name: str,
    mode: str,
    selected_weighting_method: str,
    selected_ranking_method: str,
    require_access_code: bool,
    allow_resubmission: bool,
    require_moderator_lock: bool,
    aggregation_strategy: str = "two_stage_by_group",
    created_by: str | None = None,
) -> str:
    """
    Create a new polling session and initialize stakeholder groups.
    
    Args:
        bundle: Scenario bundle
        session_name: Human-readable session name
        mode: 'single_stakeholder' or 'multi_stakeholder'
        selected_weighting_method: e.g., 'AHP'
        selected_ranking_method: e.g., 'TOPSIS'
        require_access_code: Whether access codes are required
        allow_resubmission: Whether participants can resubmit
        require_moderator_lock: Whether moderator must lock session before processing
        aggregation_strategy: 'two_stage_by_group' (recommended) or 'simple'
        created_by: Optional actor creating the session
    
    Returns:
        New session_id
    """
    save_scenario_snapshot(bundle)
    now = utc_now_iso()
    session_id = new_id("sess")
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO polling_sessions (
                session_id, scenario_id, scenario_version, session_name, mode, status,
                selected_weighting_method, selected_ranking_method, require_access_code,
                allow_resubmission, require_moderator_lock, aggregation_strategy,
                created_by, created_at, opened_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                bundle.scenario_id,
                bundle.scenario_version,
                session_name,
                mode,
                selected_weighting_method,
                selected_ranking_method,
                int(require_access_code),
                int(allow_resubmission),
                int(require_moderator_lock),
                aggregation_strategy,
                created_by,
                now,
                now,
                now,
            ),
        )
    
    # Initialize stakeholder groups from bundle defaults
    initialize_session_stakeholder_groups(session_id, bundle)
    
    return session_id


def get_session(session_id: str) -> dict[str, Any] | None:
    """Retrieve a session by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM polling_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row_to_dict(row)


def update_session_status(session_id: str, status: str) -> None:
    """Update session status and related timestamps."""
    now = utc_now_iso()
    field_updates = "status = ?, updated_at = ?"
    params: list[Any] = [status, now]
    
    if status == "locked":
        field_updates += ", locked_at = ?"
        params.append(now)
    elif status == "completed":
        field_updates += ", completed_at = ?"
        params.append(now)
    
    params.append(session_id)
    
    with get_connection() as conn:
        conn.execute(
            f"UPDATE polling_sessions SET {field_updates} WHERE session_id = ?", params
        )


def list_sessions(
    statuses: list[str] | None = None,
    scenario_id: str | None = None,
    mode: str | None = None,
    selected_weighting_method: str | None = None,
    selected_ranking_method: str | None = None,
    require_access_code: bool | None = None,
    allow_resubmission: bool | None = None,
    require_moderator_lock: bool | None = None,
) -> list[dict[str, Any]]:
    """List polling sessions with optional filtering."""
    with get_connection() as conn:
        where_clauses = []
        params = []
        
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where_clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        
        if scenario_id is not None:
            where_clauses.append("scenario_id = ?")
            params.append(scenario_id)
        
        if mode is not None:
            where_clauses.append("mode = ?")
            params.append(mode)
        
        if selected_weighting_method is not None:
            where_clauses.append("selected_weighting_method = ?")
            params.append(selected_weighting_method)
        
        if selected_ranking_method is not None:
            where_clauses.append("selected_ranking_method = ?")
            params.append(selected_ranking_method)
        
        if require_access_code is not None:
            where_clauses.append("require_access_code = ?")
            params.append(int(require_access_code))

        if allow_resubmission is not None:
            where_clauses.append("allow_resubmission = ?")
            params.append(int(allow_resubmission))
        
        if require_moderator_lock is not None:
            where_clauses.append("require_moderator_lock = ?")
            params.append(int(require_moderator_lock))
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        query = f"SELECT * FROM polling_sessions WHERE {where_clause} ORDER BY created_at DESC"
        
        rows = conn.execute(query, params).fetchall()
    
    return [dict(r) for r in rows]


def list_submissions(session_id: str) -> list[dict[str, Any]]:
    """List all current preference submissions for a session."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT ps.*, sp.stakeholder_group_id, sp.normalized_voting_power
            FROM preference_submissions ps
            JOIN session_participants sp ON ps.participant_id = sp.participant_id
            WHERE ps.session_id = ? AND ps.is_current = 1
            ORDER BY ps.submitted_at DESC
            """,
            (session_id,),
        ).fetchall()
    
    return [dict(r) for r in rows]


def list_participants(session_id: str) -> list[dict[str, Any]]:
    """List all participants in a session."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.*, s.display_name, s.alias
            FROM session_participants p
            LEFT JOIN stakeholders s ON p.stakeholder_id = s.stakeholder_id
            WHERE p.session_id = ?
            ORDER BY p.created_at DESC
            """,
            (session_id,),
        ).fetchall()
    
    return [dict(r) for r in rows]


def list_export_records(session_id: str) -> list[dict[str, Any]]:
    """List all export records for a session."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM export_records
            WHERE session_id = ?
            ORDER BY created_at DESC
            """,
            (session_id,),
        ).fetchall()
    
    return [dict(r) for r in rows]


def save_export_record(
    session_id: str,
    export_type: str,
    contract_version: str,
    payload: dict[str, Any],
    created_by: str | None = None,
) -> str:
    """Save an export record with payload."""
    now = utc_now_iso()
    export_id = new_id("exp")
    payload_json = json.dumps(payload, indent=2)
    payload_hash = hash_text(payload_json)
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO export_records (
                export_id, session_id, export_type, contract_version,
                payload_json, payload_hash, created_at, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                export_id,
                session_id,
                export_type,
                contract_version,
                payload_json,
                payload_hash,
                now,
                created_by,
            ),
        )
    
    return export_id


def create_preprocessing_run(
    session_id: str | None,
    scenario_id: str,
    scenario_version: str,
    pipeline_id: str,
    config_hash: str,
) -> str:
    """Create a preprocessing run record."""
    now = utc_now_iso()
    run_id = new_id("run")
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO preprocessing_runs (
                run_id, session_id, scenario_id, scenario_version,
                pipeline_id, config_hash, status, started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id,
                session_id,
                scenario_id,
                scenario_version,
                pipeline_id,
                config_hash,
                now,
            ),
        )
    
    return run_id


def log_preprocessing_step(
    run_id: str,
    step_id: str,
    step_type: str,
    status: str,
    input_ref: str | None = None,
    output_ref: str | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
) -> None:
    """Log a preprocessing step."""
    now = utc_now_iso()
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO preprocessing_step_logs (
                step_log_id, run_id, step_id, step_type, status,
                input_ref, output_ref, row_count, column_count, duration_ms,
                error_code, error_message, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("log"),
                run_id,
                step_id,
                step_type,
                status,
                input_ref,
                output_ref,
                row_count,
                column_count,
                duration_ms,
                error_code,
                error_message,
                started_at or now,
                now if status in {"completed", "failed"} else None,
            ),
        )


def finish_preprocessing_run(
    run_id: str,
    status: str,
    final_output_ref: str | None = None,
    final_output_hash: str | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Mark preprocessing run as complete."""
    now = utc_now_iso()
    
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE preprocessing_runs
            SET status = ?, final_output_ref = ?, final_output_hash = ?,
                row_count = ?, column_count = ?,
                error_code = ?, error_message = ?, finished_at = ?
            WHERE run_id = ?
            """,
            (
                status,
                final_output_ref,
                final_output_hash,
                row_count,
                column_count,
                error_code,
                error_message,
                now,
                run_id,
            ),
        )


# ===== Participant & Submission Functions =====

def create_participant(
    session_id: str,
    stakeholder_group_id: str,
    default_voting_power: float,
    display_name: str | None = None,
    override_voting_power: float | None = None,
    require_access_code: bool = True,
) -> tuple[str, str | None]:
    """
    Create a new participant in a session.
    
    Returns: (participant_id, plain_access_code_or_None)
    """
    from dashboard.access_codes import generate_participant_access_code
    
    now = utc_now_iso()
    participant_id = new_id("part")
    stakeholder_id = None
    
    # Optionally record display name as a stakeholder
    if display_name:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT stakeholder_id FROM stakeholders WHERE display_name = ?",
                (display_name,),
            ).fetchone()
            if row:
                stakeholder_id = row["stakeholder_id"]
            else:
                stakeholder_id = new_id("stk")
                conn.execute(
                    """
                    INSERT INTO stakeholders (stakeholder_id, display_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (stakeholder_id, display_name, now, now),
                )

    code = None
    code_hash = None
    code_hint = None
    if require_access_code:
        code, code_hash, code_hint = generate_participant_access_code()
    
    effective = override_voting_power if override_voting_power is not None else default_voting_power

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO session_participants (
                participant_id, session_id, stakeholder_id, stakeholder_group_id,
                access_code_hash, access_code_hint, status,
                default_voting_power, override_voting_power, effective_voting_power,
                invited_at, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'invited', ?, ?, ?, ?, ?, ?)
            """,
            (
                participant_id,
                session_id,
                stakeholder_id,
                stakeholder_group_id,
                code_hash,
                code_hint,
                default_voting_power,
                override_voting_power,
                effective,
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO voting_power_assignments (
                assignment_id, session_id, participant_id, stakeholder_group_id,
                assignment_scope, source, voting_power, created_at
            ) VALUES (?, ?, ?, ?, 'participant', ?, ?, ?)
            """,
            (
                new_id("vpa"),
                session_id,
                participant_id,
                stakeholder_group_id,
                "moderator_override" if override_voting_power is not None else "scenario_default",
                effective,
                now,
            ),
        )
    
    return participant_id, code


def find_participant_by_access_code(session_id: str, access_code: str) -> dict[str, Any] | None:
    """Find a participant in a session by their access code."""
    from dashboard.utils.ids import hash_access_code
    
    code_hash = hash_access_code(access_code)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM session_participants
            WHERE session_id = ? AND access_code_hash = ?
            """,
            (session_id, code_hash),
        ).fetchone()
    return row_to_dict(row)


def regenerate_participant_access_code(participant_id: str) -> str | None:
    """Regenerate access code for a participant. Returns the new plain code."""
    from dashboard.access_codes import generate_participant_access_code
    
    participant = _get_participant(participant_id)
    if not participant:
        raise RepositoryError(f"Unknown participant: {participant_id}")
    
    code, code_hash, code_hint = generate_participant_access_code()
    now = utc_now_iso()
    
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE session_participants
            SET access_code_hash = ?, access_code_hint = ?, access_code_regenerated_at = ?,
                updated_at = ?
            WHERE participant_id = ?
            """,
            (code_hash, code_hint, now, now, participant_id),
        )
    
    return code


def _get_participant(participant_id: str) -> dict[str, Any] | None:
    """Get a participant by ID (internal use)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM session_participants WHERE participant_id = ?",
            (participant_id,),
        ).fetchone()
    return row_to_dict(row)


def normalize_voting_power(session_id: str) -> None:
    """
    Recalculate normalized_voting_power for all participants in a session.
    Excludes excluded/expired participants.
    """
    participants = list_participants(session_id)
    eligible = [p for p in participants if p["status"] not in {"excluded", "expired"}]
    total = sum(float(p["effective_voting_power"] or 0) for p in eligible)
    now = utc_now_iso()
    
    with get_connection() as conn:
        for p in participants:
            if p in eligible and total > 0:
                normalized = float(p["effective_voting_power"] or 0) / total
            else:
                normalized = 0.0
            conn.execute(
                """
                UPDATE session_participants
                SET normalized_voting_power = ?, updated_at = ?
                WHERE participant_id = ?
                """,
                (normalized, now, p["participant_id"]),
            )

def _normalize_voting_power_with_conn(conn, session_id: str) -> None:
    participants = conn.execute(
        """
        SELECT participant_id, status, effective_voting_power
        FROM session_participants
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchall()

    eligible = [p for p in participants if p["status"] not in {"excluded", "expired"}]
    total = sum(float(p["effective_voting_power"] or 0) for p in eligible)
    now = utc_now_iso()

    for p in participants:
        if p in eligible and total > 0:
            normalized = float(p["effective_voting_power"] or 0) / total
        else:
            normalized = 0.0
        conn.execute(
            """
            UPDATE session_participants
            SET normalized_voting_power = ?, updated_at = ?
            WHERE participant_id = ?
            """,
            (normalized, now, p["participant_id"]),
        )

def get_current_submission(
    session_id: str, participant_id: str
) -> dict[str, Any] | None:
    """Get the current (is_current=1) preference submission for a participant."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM preference_submissions
            WHERE session_id = ? AND participant_id = ? AND is_current = 1
            """,
            (session_id, participant_id),
        ).fetchone()
    return row_to_dict(row)


def get_submission_by_id(submission_id: str) -> dict[str, Any] | None:
    """Get a submission by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM preference_submissions WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
    return row_to_dict(row)


def list_submissions_for_participant(
    session_id: str, participant_id: str
) -> list[dict[str, Any]]:
    """List all submissions (current and historical) for a participant."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM preference_submissions
            WHERE session_id = ? AND participant_id = ?
            ORDER BY submitted_at DESC
            """,
            (session_id, participant_id),
        ).fetchall()
    return [dict(r) for r in rows]

def _create_stakeholder_and_participant_no_access_code(
    conn,
    participant_id: str,
    stakeholder_id: str,
    display_name: str,
    alias: str | None,
    session_id: str,
    stakeholder_group_id: str,
    default_voting_power: float,
) -> None:
    """Create both a stakeholder and participant record in a single transaction."""
    now = utc_now_iso()
    
    conn.execute(
        """
        INSERT INTO stakeholders (
        stakeholder_id, display_name, alias, external_ref, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, ?)
        """,
        (stakeholder_id, display_name, alias, now, now),
    )
    
    conn.execute(
        """
        INSERT INTO session_participants (
            participant_id, session_id, stakeholder_id, stakeholder_group_id,
            access_code_hash, access_code_hint, status,
            default_voting_power, override_voting_power, effective_voting_power,
            invited_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, NULL, 'started', ?, NULL, ?, ?, ?, ?)
        """,
        (
            participant_id,
            session_id,
            stakeholder_id,
            stakeholder_group_id,
            default_voting_power,
            default_voting_power,
            now,
            now,
            now,
        ),
    )

    conn.execute(
        """
        INSERT INTO voting_power_assignments (
            assignment_id, session_id, participant_id, stakeholder_group_id,
            assignment_scope, source, voting_power, created_at
        ) VALUES (?, ?, ?, ?, 'participant', 'scenario_default', ?, ?)
        """,
        (
            new_id("vpa"),
            session_id,
            participant_id,
            stakeholder_group_id,
            default_voting_power,
            now,
        ),
    )

def _create_stakeholder_and_participant_with_access_code(
    conn,
    participant_id: str,
    stakeholder_id: str,
    display_name: str,
    alias: str | None,
    session_id: str,
    stakeholder_group_id: str,
    effective_voting_power: float,
    external_ref: str | None = None,
    access_code_hash: str | None = None,
    source: str = "scenario_default",
) -> None:
    """Create both a stakeholder and participant record with access code."""
    now = utc_now_iso()
    
    conn.execute(
        """
        INSERT INTO stakeholders (
        stakeholder_id, display_name, alias, external_ref, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (stakeholder_id, display_name, alias, external_ref, now, now),
    )
    
    conn.execute(
        """
        INSERT INTO session_participants (
            participant_id, session_id, stakeholder_id, stakeholder_group_id,
            access_code_hash, status, default_voting_power,
            override_voting_power, effective_voting_power,
            invited_at, started_at, created_at, submitted_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'submitted', ?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            participant_id,
            session_id,
            stakeholder_id,
            stakeholder_group_id,
            access_code_hash,
            effective_voting_power,
            effective_voting_power,
            now,
            now,
            now,
            now,
            now,
        ),
    )

    conn.execute(
        """
        INSERT INTO voting_power_assignments (
            assignment_id, session_id, participant_id, stakeholder_group_id,
            assignment_scope, source, voting_power, reason, assigned_by, created_at
        ) VALUES (?, ?, ?, ?, 'participant', ?, ?, 'Created by submission import', 'moderator', ?)
        """,
        (
            new_id("vpa"),
            session_id,
            participant_id,
            stakeholder_group_id,
            source,
            effective_voting_power,
            now,
        ),
    )

def _create_stakeholder_for_participant(
    conn,
    participant_id: str,
    stakeholder_id: str,
    display_name: str,
    alias: str | None = None,
) -> None:
    """Create a stakeholder record for a participant."""
    now = utc_now_iso()
    
    conn.execute(
        """
        INSERT INTO stakeholders (stakeholder_id, display_name, alias, external_ref, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, ?)
        """,
        (stakeholder_id, display_name, alias, now, now),
    )

    conn.execute(
        """
        UPDATE session_participants
        SET stakeholder_id = ?,
            status = CASE
                WHEN status = 'invited' THEN 'started'
                ELSE status
            END,
            started_at = COALESCE(started_at, ?),
            updated_at = ?
        WHERE participant_id = ?
        """,
        (stakeholder_id, now, now, participant_id),
    )

def submit_preferences_atomic(
    session_id: str,
    stakeholder_info: dict[str, Any],
    preference_method: str,
    raw_preferences: dict[str, Any],
    transformed_preferences: dict[str, Any],
    allow_resubmission: bool = False,
) -> dict[str, str]:
    """
    Submit preference data for a participant.
    
    Performs atomic operations:
    1. Check session is open
    2. Handle existing submission (if resubmission allowed)
    3. Insert new submission
    4. Update participant status
    5. Recalculate normalized voting power
    
    Returns: dict with submission_id and participant_id
    """
    with get_connection() as conn:
        session = conn.execute(
            "SELECT * FROM polling_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not session:
            raise RepositoryError(f"Unknown session: {session_id}")
        if session["status"] != "open":
            raise RepositoryError("Session is not open for submissions")

        participant_id = stakeholder_info.get("participant_id")
        
        existing = conn.execute(
            """
            SELECT * FROM preference_submissions
            WHERE session_id = ? AND participant_id = ? AND is_current = 1
            """,
            (session_id, participant_id),
        ).fetchone() if participant_id else None
        
        if existing and not allow_resubmission:
            raise RepositoryError("Duplicate submission blocked for this participant")

        group_id = stakeholder_info.get("group_id")
        
        if session["require_access_code"]:
            # Sessions with access codes: verify participant exists
            if not participant_id:
                raise RepositoryError("Participant ID is required for sessions with access code")
            
            participant = conn.execute(
                "SELECT * FROM session_participants WHERE participant_id = ?",
                (participant_id,),
            ).fetchone()
            if not participant:
                raise RepositoryError(f"Unknown participant: {participant_id}")
            if participant["stakeholder_group_id"] != group_id:
                raise RepositoryError("Expected participant group does not match submission data")
            if participant["status"] in ["excluded", "expired"]:
                raise RepositoryError("Participant is not eligible to submit preferences")
            if participant["access_code_hash"] is None:
                raise RepositoryError("Participant must submit access code to submit preferences")
            
            stakeholder_id = new_id("stk")
            _create_stakeholder_for_participant(conn,participant_id, stakeholder_id, stakeholder_info["name"], stakeholder_info["alias"])

        else:
            # Sessions without access codes: create new stakeholder and participant
            if session["mode"] == "single_stakeholder":
                active_count = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM session_participants
                    WHERE session_id = ?
                        AND status NOT IN ('excluded', 'expired')
                    """,
                    (session_id,),
                ).fetchone()["count"]

                if active_count >= 1:
                    raise RepositoryError("Session is single-stakeholder and already has a submission")
                
            stakeholder_id = new_id("stk")
            participant_id = new_id("part")

            _create_stakeholder_and_participant_no_access_code(
                conn,
                participant_id,
                stakeholder_id,
                stakeholder_info.get("name"),
                stakeholder_info.get("alias"),
                session_id,
                group_id,
                stakeholder_info.get("voting_power", 1.0),
            )

        # Check for other submissions in single-stakeholder mode
        if session["mode"] == "single_stakeholder":
            other_submission_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM preference_submissions
                WHERE session_id = ?
                    AND is_current = 1
                    AND participant_id <> ?
                """,
                (session_id, participant_id),
            ).fetchone()["count"]

            if other_submission_count >= 1:
                raise RepositoryError("Session is single-stakeholder and already has a submission")

        now = utc_now_iso()
        # Update existing submission to is_current=0 if resubmission allowed, then insert new submission
        if existing and allow_resubmission:
            conn.execute(
                """
                UPDATE preference_submissions
                SET is_current = 0, updated_at = ?
                WHERE submission_id = ?
                """,
                (now, existing["submission_id"]),
            )
        
        submission_id = new_id("sub")
        
        conn.execute(
            """
            INSERT INTO preference_submissions (
                submission_id, session_id, participant_id, preference_method,
                raw_preferences_json, transformed_preferences_json,
                validation_status, is_current, submitted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'valid', 1, ?, ?)
            """,
            (
                submission_id,
                session_id,
                participant_id,
                preference_method,
                json.dumps(raw_preferences, indent=2),
                json.dumps(transformed_preferences, indent=2),
                now,
                now,
            ),
        )
    
        conn.execute(
            """
            UPDATE session_participants
            SET status = 'submitted', submitted_at = ?, updated_at = ?
            WHERE participant_id = ?
            """,
            (now, now, participant_id),
        )

        # Recalculate normalized voting power
        _normalize_voting_power_with_conn(conn, session_id)
    
    return {
        "session_id": session_id,
        "submission_id": submission_id,
        "participant_id": participant_id,
    }

def import_submission_atomic(
        *,
        session_id: str,
        stakeholder_group_id: str,
        display_name: str,
        alias: str | None,
        external_ref: str | None,
        default_voting_power: float,
        raw_preferences: dict[str, Any],
        transformed_preferences: dict[str, Any],
        preference_method: str,
        generate_access_code: bool = False,
        allow_locked_session: bool = False,
        source: str = "bulk_import",
) -> dict[str, Any]:
    
    now = utc_now_iso()

    with get_connection() as conn:
        session = conn.execute(
            "SELECT * FROM polling_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not session:
            raise RepositoryError(f"Unknown session: {session_id}")
        
        if session["status"] != "open":
            if not (allow_locked_session and session["status"] == "locked"):
                raise RepositoryError(
                    "Imports are only allowed for open sessions unless locked-session testing is enabled."
                )
        
        if session["mode"] == "single_stakeholder":
            active_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM session_participants
                WHERE session_id = ?
                  AND status NOT IN ('excluded', 'expired')
                """,
                (session_id,),
            ).fetchone()["count"]

            if active_count >= 1:
                raise RepositoryError(
                    "Cannot import multiple rows into a single-stakeholder session."
                )
        
        # Latest group-level session weight, if available.
        group_weight_row = conn.execute(
            """
            SELECT voting_power
            FROM voting_power_assignments
            WHERE session_id = ?
              AND stakeholder_group_id = ?
              AND assignment_scope = 'group'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id, stakeholder_group_id),
        ).fetchone()

        effective_voting_power = (
                float(group_weight_row["voting_power"])
                if group_weight_row
                else float(default_voting_power)
        )

        access_code = None
        access_code_hash = None

        if generate_access_code:
            access_code = generate_access_code_value()
            access_code_hash = hash_access_code(access_code)

        stakeholder_id = new_id("stk")
        participant_id = new_id("part")
        submission_id = new_id("sub")

        _create_stakeholder_and_participant_with_access_code(
            conn,
            participant_id,
            stakeholder_id,
            display_name,
            alias,
            session_id,
            stakeholder_group_id,
            effective_voting_power,
            external_ref,
            access_code_hash,
            source,
        )

        conn.execute(
            """
            INSERT INTO preference_submissions (
                submission_id, session_id, participant_id, submission_version,
                preference_method, raw_preferences_json, transformed_preferences_json,
                validation_status, is_current, submitted_at, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, 'valid', 1, ?, ?)
            """,
            (
                submission_id,
                session_id,
                participant_id,
                preference_method,
                json.dumps(raw_preferences, indent=2),
                json.dumps(transformed_preferences, indent=2),
                now,
                now,
            ),
        )

        _normalize_voting_power_with_conn(conn, session_id)
    
    result = {
        "row_status": "imported",
        "session_id": session_id,
        "participant_id": participant_id,
        "submission_id": submission_id,
        "stakeholder_group_id": stakeholder_group_id,
        "display_name": display_name,
    }

    if access_code:
        result["access_code"] = access_code

    return result