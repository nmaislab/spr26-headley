from __future__ import annotations

import json
import time

import pandas as pd

from pathlib import Path
from typing import Any

from dashboard.scenario_loader import ScenarioBundle, safe_resolve
from dashboard.utils.ids import hash_text
from dashboard.utils.time import utc_now_iso
from dashboard.function_loader import get_approved_function_decl, load_approved_function
from dashboard.repositories import create_preprocessing_run, log_preprocessing_step, finish_preprocessing_run

class PreprocessingError(RuntimeError):
    pass

SUPPORTED_STEPS = {
    "load_csv",
    "load_excel",
    "load_sql",
    "load_mongodb",
    "load_api",
    "rename_columns",
    "select_columns",
    "join",
    "convert_types",
    "missing_values",
    "derive_column",
    "aggregate",
    "normalize",
    "approved_function",
}

def data_source_map(bundle: ScenarioBundle) -> dict[str, dict[str, Any]]:
    return {source["id"]: source for source in bundle.data_sources.get("data_sources", [])}

def validate_pipeline_config(config: dict[str, Any]) -> None:
    """Validates the structure of the preprocessing pipeline configuration."""
    if not config:
        raise PreprocessingError("Missing pipeline configuration.")
    if "steps" not in config or not isinstance(config["steps"], list):
        raise PreprocessingError("preprocessing.json must include a list of steps under the 'steps' key.")
    if "final_output" not in config or "table" not in config["final_output"]:
        raise PreprocessingError("preprocessing.json must include a final_output.table key specifying the name of the final output table.")
    for step in config["steps"]:
        if "id" not in step or "type" not in step:
            raise PreprocessingError("Each step must include an 'id' and 'type'.")
        if step["type"] not in SUPPORTED_STEPS:
            raise PreprocessingError(f"Unsupported step type: {step['type']}. Supported types are: {', '.join(SUPPORTED_STEPS)}.")
        
def execute_preprocessing(bundle: ScenarioBundle, session_id: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Executes the preprocessing pipeline defined in the scenario bundle and returns the final preprocessed DataFrame along with metadata.
    """
    if bundle.preprocessing is None:
        raise PreprocessingError("No preprocessing configuration found in the scenario bundle.")
    
    config = bundle.preprocessing
    validate_pipeline_config(config)
    config_hash = hash_text(json.dumps(config, sort_keys=True))
    run_id = create_preprocessing_run(
        session_id=session_id,
        scenario_id=bundle.scenario_id,
        scenario_version=bundle.scenario_version,
        pipeline_id=config.get("pipeline_id", "default_pipeline"),
        config_hash=config_hash,
    )

    context: dict[str, pd.DataFrame] = {}
    source_by_id = data_source_map(bundle)
    metadata_steps: list[dict[str, Any]] = []

    try:
        for step in config["steps"]:
            started = utc_now_iso()
            t0 = time.perf_counter()
            try:
                output_name, df = execute_step(bundle, config, step, context, source_by_id)
                context[output_name] = df
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_preprocessing_step(
                    run_id=run_id,
                    step_id=step["id"],
                    step_type=step["type"],
                    status="success",
                    input_ref=str(step.get("input") or step.get("source_ref") or step.get("left") or ""),
                    output_ref=output_name,
                    row_count=len(df),
                    column_count=len(df.columns),
                    duration_ms=duration_ms,
                    started_at=started,
                )
                metadata_steps.append(
                    {
                        "step_id": step["id"],
                        "step_type": step["type"],
                        "status": "success",
                        "output": output_name,
                        "rows": len(df),
                        "columns": len(df.columns),
                        "duration_ms": duration_ms,
                    }
                )
            except Exception as exc:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_preprocessing_step(
                    run_id=run_id,
                    step_id=step.get("id", "unknown"),
                    step_type=step.get("type", "unknown"),
                    status="failed",
                    input_ref=str(step.get("input") or step.get("source_ref") or step.get("left") or ""),
                    output_ref=step.get("output"),
                    duration_ms=duration_ms,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    started_at=started,
                )
                raise
    
        final_table_name = config["final_output"]["table"]

        if final_table_name not in context:
            raise PreprocessingError(
                f"Final output table '{final_table_name}' was not produced"
            )

        final_df = context[final_table_name]
        validate_final_output(final_df, config)

        final_output_config = config.get("final_output", {})
        alternative_id_column = final_output_config.get("alternative_id_column")
        criteria_columns = final_output_config.get("criteria_columns", [])

        final_payload = final_df.to_json(orient="records")
        final_hash = hash_text(final_payload)

        finish_preprocessing_run(
            run_id=run_id,
            status="success",
            final_output_ref=final_table_name,
            final_output_hash=final_hash,
            row_count=len(final_df),
            column_count=len(final_df.columns),
        )

        metadata = {
            "run_id": run_id,
            "pipeline_id": config.get("pipeline_id", "default_pipeline"),
            "status": "success",
            "config_hash": config_hash,
            "final_output": final_table_name,
            "decision_matrix": {
                "alternative_id_column": alternative_id_column,
                "criteria_columns": criteria_columns,
                "row_count": len(final_df),
                "column_count": len(final_df.columns),
                "columns": list(final_df.columns),
                "hash": final_hash,
            },
            "step_logs": metadata_steps,
        }

        return final_df, metadata

    except Exception as exc:
        finish_preprocessing_run(
            run_id=run_id,
            status="failed",
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        raise PreprocessingError(str(exc)) from exc
    
def execute_step(
        bundle: ScenarioBundle,
        config: dict[str, Any],
        step: dict[str, Any],
        context: dict[str, pd.DataFrame],
        source_by_id: dict[str, dict[str, Any]],
) -> tuple[str, pd.DataFrame]:
    step_type = step["type"]

    if step_type == "load_csv":
        source = source_by_id.get(step["source_ref"])
        if not source:
            raise PreprocessingError(f"Unknown data source ref: {step['source_ref']}")
        path = safe_resolve(bundle.folder, source.get("path"))
        if path is None or not path.exists():
            raise PreprocessingError(f"CSV file not found: {source.get('path')}")
        df = pd.read_csv(path, **step.get("options", {}))
        return step["output"], df

    if step_type == "load_excel":
        source = source_by_id.get(step["source_ref"])
        if not source:
            raise PreprocessingError(f"Unknown data source ref: {step['source_ref']}")
        path = safe_resolve(bundle.folder, source.get("path"))
        if path is None or not path.exists():
            raise PreprocessingError(f"Excel file not found: {source.get('path')}")
        df = pd.read_excel(path, **step.get("options", {}))
        return step["output"], df

    if step_type in {"load_sql", "load_mongodb", "load_api"}:
        # TODO: Work on external connections at a later date
        raise PreprocessingError(f"{step_type} connector is not currently implemented")

    if step_type == "rename_columns":
        df = require_table(context, step["input"]).copy()
        df = df.rename(columns=step.get("columns", {}))
        return step["output"], df

    if step_type == "select_columns":
        df = require_table(context, step["input"]).copy()
        columns = step.get("columns", [])
        missing = [col for col in columns if col not in df.columns]
        if missing:
            raise PreprocessingError(f"select_columns missing columns: {missing}")
        return step["output"], df[columns]

    if step_type == "join":
        left = require_table(context, step["left"])
        right = require_table(context, step["right"])
        on = step.get("on")
        if not on:
            raise PreprocessingError("join step requires 'on'")
        df = left.merge(right, how=step.get("how", "inner"), on=on)
        return step["output"], df

    if step_type == "convert_types":
        df = require_table(context, step["input"]).copy()
        for col, dtype in step.get("columns", {}).items():
            if col not in df.columns:
                raise PreprocessingError(f"convert_types missing column: {col}")
            if dtype in {"float", "int", "numeric"}:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                if dtype == "int":
                    df[col] = df[col].astype("Int64")
            else:
                df[col] = df[col].astype(dtype)
        return step["output"], df

    if step_type == "missing_values":
        df = require_table(context, step["input"]).copy()
        strategy = step.get("strategy", {})
        default = strategy.get("default", "error")
        column_strategies = strategy.get("columns", {})
        for col in df.columns:
            action = column_strategies.get(col, default)
            if not df[col].isna().any():
                continue
            if action == "error":
                raise PreprocessingError(f"Null values found in required column: {col}")
            if action == "drop_rows":
                df = df.dropna(subset=[col])
            elif action == "median":
                df[col] = df[col].fillna(df[col].median(numeric_only=True))
            elif action == "mean":
                df[col] = df[col].fillna(df[col].mean(numeric_only=True))
            elif action == "zero":
                df[col] = df[col].fillna(0)
            elif action == "keep":
                pass
            else:
                raise PreprocessingError(f"Unsupported missing value strategy '{action}' for {col}")
        return step["output"], df

    if step_type == "derive_column":
        df = require_table(context, step["input"]).copy()
        new_column = step["new_column"]
        operation = step["operation"]
        operands = step.get("operands", [])
        df[new_column] = apply_operation(df, operation, operands, step)
        return step["output"], df

    if step_type == "normalize":
        df = require_table(context, step["input"]).copy()
        method = step.get("method", "minmax")
        for col in step.get("columns", []):
            if col not in df.columns:
                raise PreprocessingError(f"normalize missing column: {col}")
            series = pd.to_numeric(df[col], errors="coerce")
            if method == "minmax":
                denom = series.max() - series.min()
                df[col] = 0 if denom == 0 else (series - series.min()) / denom
            elif method == "zscore":
                denom = series.std()
                df[col] = 0 if denom == 0 else (series - series.mean()) / denom
            else:
                raise PreprocessingError(f"Unsupported normalization method: {method}")
        return step["output"], df

    if step_type == "aggregate":
        df = require_table(context, step["input"]).copy()
        group_by = step.get("group_by", [])
        aggregations = step.get("aggregations", {})
        if not aggregations:
            raise PreprocessingError("aggregate step requires aggregations")
        df = df.groupby(group_by, dropna=False).agg(aggregations).reset_index()
        # Flatten MultiIndex columns if needed.
        df.columns = ["_".join(c).strip("_") if isinstance(c, tuple) else c for c in df.columns]
        return step["output"], df

    if step_type == "approved_function":
        df = require_table(context, step["input"]).copy()
        function_ref = step["function_ref"]
        decl = get_approved_function_decl(config, function_ref)
        func = load_approved_function(bundle.folder, decl)
        result = func(df)
        if not isinstance(result, pd.DataFrame):
            raise PreprocessingError(f"Approved function {function_ref} must return a pandas DataFrame")
        return step["output"], result

    raise PreprocessingError(f"Unsupported step type: {step_type}")

def require_table(context: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    if name not in context:
        raise PreprocessingError(f"Input table not found in pipeline context: {name}")
    return context[name]

def apply_operation(df: pd.DataFrame, operation: str, operands: list[str], step: dict[str, Any]) -> Any:
    for col in operands:
        if col not in df.columns and not isinstance(col, (int, float)):
            raise PreprocessingError(f"derived_column operand missing: {col}")
        
    if operation == "divide":
        numerator = pd.to_numeric(df[operands[0]], errors="coerce")
        denominator = pd.to_numeric(df[operands[1]], errors="coerce")
        result = numerator / denominator.replace({0: pd.NA})
        return result.fillna(0) if step.get("on_zero") == "zero" else result
    if operation == "add":
        result = 0
        for col in operands:
            result = result + pd.to_numeric(df[col], errors="coerce")
        return result
    if operation == "subtract":
        return pd.to_numeric(df[operands[0]], errors="coerce") - pd.to_numeric(df[operands[1]], errors="coerce")
    if operation == "multiply":
        result = 1
        for col in operands:
            result = result * pd.to_numeric(df[col], errors="coerce")
        return result
    if operation == "absolute_difference":
        return (pd.to_numeric(df[operands[0]], errors="coerce") - pd.to_numeric(df[operands[1]], errors="coerce")).abs()

    raise PreprocessingError(f"Unsupported derive operation: {operation}")

def validate_final_output(df: pd.DataFrame, config: dict[str, Any]) -> None:
    validation = config.get("validation", {})
    required = validation.get("required_columns", [])
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise PreprocessingError(f"Final output missing required columns: {missing}")
    minimum_rows = validation.get("minimum_rows")
    if minimum_rows and len(df) < minimum_rows:
        raise PreprocessingError(f"Final output has {len(df)} rows, expected at least {minimum_rows}")
    no_nulls = validation.get("no_nulls_in", [])
    null_cols = [col for col in no_nulls if col in df.columns and df[col].isna().any()]
    if null_cols:
        raise PreprocessingError(f"Final output has nulls in required columns: {null_cols}")