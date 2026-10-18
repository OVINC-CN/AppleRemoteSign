import re
from collections.abc import Iterable

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
BEARER = re.compile(r"(?i)(bearer\s+)\S+")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
SIGNED_URL = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)
API_KEY = re.compile(r"\bars_[A-Za-z0-9_-]{32,}\b")
URL_PASSWORD = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s/:@]*:)[^\s@]+(@)")


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self.secrets = sorted({secret for secret in secrets if secret}, key=len, reverse=True)

    def clean(self, text: str) -> str:
        text = ANSI.sub("", text)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = BEARER.sub(r"\1[REDACTED]", text)
        text = JWT.sub("[REDACTED JWT]", text)
        text = API_KEY.sub("[REDACTED API KEY]", text)
        text = URL_PASSWORD.sub(r"\1[REDACTED]\2", text)
        return SIGNED_URL.sub(r"\1?[REDACTED]", text)
