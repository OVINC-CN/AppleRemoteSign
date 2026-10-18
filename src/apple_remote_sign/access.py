from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    user_id: str | None = None
    username: str | None = None
    is_admin: bool = False
    application_id: str | None = None
    key_id: str | None = None
    session_hash: str | None = None
    csrf_token: str | None = None
