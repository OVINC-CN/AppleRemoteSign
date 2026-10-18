import hashlib
import secrets
import time
from urllib.parse import quote

from apple_remote_sign.config import Settings


class CdnSigner:
    """Issue EdgeOne type A URLs; the CDN controls their validity period."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.cdn_base_url
        self.key = settings.cdn_sign_key
        self.parameter = settings.cdn_sign_param

    def sign(self, object_key: str) -> str:
        # Hash the exact escaped path that will be sent in the Location header.
        path = quote("/" + object_key, safe="/")
        value = f"{int(time.time())}-{secrets.token_hex(16)}-0"
        digest = hashlib.md5(
            f"{path}-{value}-{self.key.get_secret_value()}".encode(), usedforsecurity=False
        ).hexdigest()
        return f"{self.base_url}{path}?{self.parameter}={value}-{digest}"
