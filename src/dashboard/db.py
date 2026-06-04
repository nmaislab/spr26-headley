from __future__ import annotations

import os
import sqlite3

from pathlib import Path

DEFAULT_DB_PATH = Path("database/stakeholder_polling.sqlite")

def get_db_path() -> Path:
    return Path(os.getenv("POLLING_DB_PATH", str(DEFAULT_DB_PATH)))

def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db() -> None:
    """
    Create SQLite schema.

    Designed to remain portable to PostgreSQL later.
    """
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scenarios (
                scenario_id TEXT NOT NULL,
                scenario_version TEXT NOT NULL,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                config_snapshot_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scenario_id, scenario_version)
            );

            CREATE TABLE IF NOT EXISTS polling_sessions (
                session_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                scenario_version TEXT NOT NULL,
                session_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                selected_weighting_method TEXT NOT NULL,
                selected_ranking_method TEXT NOT NULL,
                require_access_code INTEGER NOT NULL DEFAULT 1,
                allow_resubmission INTEGER NOT NULL DEFAULT 0,
                require_moderator_lock INTEGER NOT NULL DEFAULT 1,
                aggregation_strategy TEXT NOT NULL DEFAULT 'simple',
                created_by TEXT,
                created_at TEXT NOT NULL,
                opened_at TEXT,
                locked_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stakeholders (
                stakeholder_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                alias TEXT,
                external_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_participants (
                participant_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                stakeholder_id TEXT,
                stakeholder_group_id TEXT NOT NULL,
                access_code_hash TEXT,
                access_code_hint TEXT,
                access_code_created_at TEXT,
                access_code_regenerated_at TEXT,
                access_code_expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'invited',
                default_voting_power REAL NOT NULL,
                override_voting_power REAL,
                effective_voting_power REAL NOT NULL,
                normalized_voting_power REAL,
                invited_at TEXT,
                started_at TEXT,
                submitted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id),
                FOREIGN KEY (stakeholder_id) REFERENCES stakeholders(stakeholder_id)
            );

            CREATE TABLE IF NOT EXISTS voting_power_assignments (
                assignment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                participant_id TEXT,
                stakeholder_group_id TEXT,
                assignment_scope TEXT NOT NULL,
                source TEXT NOT NULL,
                voting_power REAL NOT NULL,
                reason TEXT,
                assigned_by TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id),
                FOREIGN KEY (participant_id) REFERENCES session_participants(participant_id)
            );

            CREATE TABLE IF NOT EXISTS preference_submissions (
                submission_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                submission_version INTEGER NOT NULL DEFAULT 1,
                preference_method TEXT NOT NULL,
                raw_preferences_json TEXT NOT NULL,
                transformed_preferences_json TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                submitted_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id),
                FOREIGN KEY (participant_id) REFERENCES session_participants(participant_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_current_submission
            ON preference_submissions(session_id, participant_id, is_current)
            WHERE is_current = 1;

            CREATE TABLE IF NOT EXISTS preprocessing_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT,
                scenario_id TEXT NOT NULL,
                scenario_version TEXT NOT NULL,
                pipeline_id TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                final_output_ref TEXT,
                final_output_hash TEXT,
                row_count INTEGER,
                column_count INTEGER,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_code TEXT,
                error_message TEXT,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS preprocessing_step_logs (
                step_log_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                step_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_ref TEXT,
                output_ref TEXT,
                row_count INTEGER,
                column_count INTEGER,
                duration_ms INTEGER,
                error_code TEXT,
                error_message TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (run_id) REFERENCES preprocessing_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS export_records (
                export_id TEXT PRIMARY KEY,
                session_id TEXT,
                export_type TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                payload_json TEXT,
                file_path TEXT,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                audit_id TEXT PRIMARY KEY,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                session_id TEXT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                before_json TEXT,
                after_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_stakeholder_groups (
                session_id TEXT NOT NULL,
                stakeholder_group_id TEXT NOT NULL,
                display_name TEXT,
                default_group_voting_power REAL NOT NULL DEFAULT 1.0,
                current_group_voting_power REAL NOT NULL DEFAULT 1.0,
                normalized_group_voting_power REAL NOT NULL DEFAULT 0.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, stakeholder_group_id),
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS stakeholder_group_weight_history (
                history_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                stakeholder_group_id TEXT NOT NULL,
                old_group_voting_power REAL,
                new_group_voting_power REAL NOT NULL,
                reason TEXT,
                changed_by TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS stakeholder_group_aggregation_results (
                result_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                export_id TEXT,
                stakeholder_group_id TEXT,
                aggregation_level TEXT NOT NULL,
                criteria_order_json TEXT NOT NULL,
                pairwise_matrix_json TEXT NOT NULL,
                weights_json TEXT,
                consistency_ratio REAL,
                is_consistent INTEGER,
                submission_count INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES polling_sessions(session_id),
                FOREIGN KEY (export_id) REFERENCES export_records(export_id)
            );
            """
        )