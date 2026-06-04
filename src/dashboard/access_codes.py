from __future__ import annotations

from dashboard.utils.ids import generate_access_code, hash_access_code


def generate_participant_access_code() -> tuple[str, str, str]:
    """
    Returns:
    - plain code to show once
    - hash to store
    - hint to help moderator identify the code later
    """
    code = generate_access_code(length=10)
    code_hash = hash_access_code(code)
    hint = code[-4:]
    return code, code_hash, hint