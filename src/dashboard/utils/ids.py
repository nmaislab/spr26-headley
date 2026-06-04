from __future__ import annotations

import hashlib
import secrets
import string
import uuid

def new_id(prefix: str) -> str:
    """Create a stable, unique ID for prototype records."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def generate_access_code(length: int = 8) -> str:
    """Generate a human-friendly access code."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def hash_access_code(code: str) -> str:
    """Hash access codes before storage. Prototype only, not for production use."""
    normalized = code.strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
