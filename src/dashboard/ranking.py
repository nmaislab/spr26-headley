from __future__ import annotations

from typing import Any
from pyDecision.algorithm import topsis_method

import numpy as np
import pandas as pd

class RankingError(RuntimeError):
    pass

def _criterion_directions_from_bundle(bundle) -> dict[str, str]:
    return {
        c["id"]: c.get("direction") or c.get("criteria_type") or "benefit"
        for c in bundle.criteria
    }

def _build_criterion_type_vector(
    *,
    criteria_order: list[str],
    criterion_directions: dict[str, str],
) -> list[str]:
    """
    pyDecision TOPSIS expects criterion types.

    In many pyDecision examples:
        'max' means benefit
        'min' means cost
    """
    result = []

    for criterion_id in criteria_order:
        direction = criterion_directions.get(criterion_id, "benefit").lower()

        if direction in {"benefit", "max", "maximize"}:
            result.append("max")
        elif direction in {"cost", "min", "minimize"}:
            result.append("min")
        else:
            raise RankingError(
                f"Unknown criterion direction for {criterion_id}: {direction}"
            )

    return result

def run_topsis_for_weights(
    *,
    bundle,
    decision_matrix_df: pd.DataFrame,
    weights_by_criterion: dict[str, float],
    alternative_id_column: str,
) -> dict[str, Any]:
    """
    Run TOPSIS using one weight vector.

    This is used for:
    - one stakeholder's AHP-derived weights
    - the aggregated/group AHP-derived weights
    """
    criteria_order = list(weights_by_criterion.keys())

    missing_cols = [
        criterion_id
        for criterion_id in criteria_order
        if criterion_id not in decision_matrix_df.columns
    ]

    if missing_cols:
        raise RankingError(
            f"Decision matrix is missing criteria columns required by weights: {missing_cols}"
        )

    if alternative_id_column not in decision_matrix_df.columns:
        raise RankingError(
            f"Decision matrix is missing alternative id column: {alternative_id_column}"
        )

    criterion_directions = _criterion_directions_from_bundle(bundle)
    criterion_types = _build_criterion_type_vector(
        criteria_order=criteria_order,
        criterion_directions=criterion_directions,
    )

    dataset = decision_matrix_df[criteria_order].astype(float).to_numpy()
    weights = [float(weights_by_criterion[c]) for c in criteria_order]

    # pyDecision returns a ranked dataset-like result depending on version.
    # Keep raw output and also compute a safe display table.
    topsis_result = topsis_method(
        dataset,
        weights,
        criterion_types,
        graph=False,
        verbose=False,
    )

    alternatives = decision_matrix_df[alternative_id_column].astype(str).tolist()

    result_df = _coerce_topsis_output_to_dataframe(
        topsis_result=topsis_result,
        alternatives=alternatives,
    )

    return {
        "method": "TOPSIS",
        "alternative_id_column": alternative_id_column,
        "criteria_order": criteria_order,
        "criterion_types": criterion_types,
        "weights": weights_by_criterion,
        "rankings": result_df.to_dict(orient="records"),
        "raw_result_repr": repr(topsis_result),
    }

def _coerce_topsis_output_to_dataframe(
    *,
    topsis_result: Any,
    alternatives: list[str],
) -> pd.DataFrame:
    """
    pyDecision return formats can vary by version.

    This helper keeps the application from breaking if the return is:
    - ndarray
    - list
    - dataframe-like
    """
    if isinstance(topsis_result, pd.DataFrame):
        df = topsis_result.copy()
        if "Alternative" not in df.columns and len(df) == len(alternatives):
            df.insert(0, "Alternative", alternatives)
        return df

    arr = np.asarray(topsis_result)

    if arr.ndim == 1 and len(arr) == len(alternatives):
        df = pd.DataFrame(
            {
                "Alternative": alternatives,
                "TOPSIS Score": arr.astype(float),
            }
        )
        df["Rank"] = df["TOPSIS Score"].rank(
            ascending=False,
            method="min",
        ).astype(int)
        return df.sort_values("Rank")

    if arr.ndim == 2:
        df = pd.DataFrame(arr)
        if len(df) == len(alternatives):
            df.insert(0, "Alternative", alternatives)
        return df

    return pd.DataFrame(
        {
            "Alternative": alternatives,
            "TOPSIS Output": [repr(topsis_result)] * len(alternatives),
        }
    )

def run_fuzzy_topsis_for_weights(
    *,
    bundle,
    decision_matrix_df: pd.DataFrame,
    weights_by_criterion: dict[str, float],
    alternative_id_column: str,
) -> dict[str, Any]:
    """
    Placeholder for Fuzzy TOPSIS.

    The fuzzy path should be implemented once the scenario decision matrix
    includes fuzzy values or fuzzy intervals.
    """
    raise RankingError(
        "Fuzzy TOPSIS is not implemented yet. Use TOPSIS for the current prototype."
    )