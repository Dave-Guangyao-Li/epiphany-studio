from __future__ import annotations

from hashlib import sha256
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def stable_id(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"
