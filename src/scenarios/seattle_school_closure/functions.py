from __future__ import annotations

import numpy as np
import pandas as pd

def compute_school_closure_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Approved scenario-local custom function.

    Adds:
    - retention_rate = grade_12 / grade_9
    - grade_imbalance = coefficient of variation across grades 9-12

    This function is explicitly allowlisted in preprocessing.json. The loader rejects
    undeclared function names and non-local files.
    """
    result = df.copy()
    result["retention_rate"] = np.where(result["grade_9"] == 0, 0, result["grade_12"] / result["grade_9"])
    grade_cols = ["grade_9", "grade_10", "grade_11", "grade_12"]
    grade_mean = result[grade_cols].mean(axis=1)
    grade_std = result[grade_cols].std(axis=1)
    result["grade_imbalance"] = np.where(grade_mean == 0, 0, grade_std / grade_mean)
    return result