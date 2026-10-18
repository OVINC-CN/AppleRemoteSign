import base64
from dataclasses import dataclass, field
from typing import Self

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import load_der_private_key
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CredentialDocument(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    issuer_id: str = Field(min_length=1)
    key_id: str = Field(pattern=r"^[A-Za-z0-9]+$")
    private_key: SecretStr = Field(min_length=1)


@dataclass(frozen=True)
class AppleCredentials:
    issuer_id: str
    key_id: str
    private_key: EllipticCurvePrivateKey = field(repr=False)
    encoded_private_key: str = field(repr=False)

    @classmethod
    def from_json(cls, content: bytes) -> Self:
        try:
            document = CredentialDocument.model_validate_json(content)
            encoded_key = document.private_key.get_secret_value()
            key = load_der_private_key(base64.b64decode(encoded_key, validate=True), password=None)
            if not isinstance(key, EllipticCurvePrivateKey) or not isinstance(key.curve, SECP256R1):
                raise ValueError("Expected an EC P-256 private key.")
            return cls(document.issuer_id, document.key_id, key, encoded_key)
        except ValueError, TypeError, UnsupportedAlgorithm:
            raise RuntimeError(
                "APPLE_KEY_FILE must contain rcodesign credential JSON with issuer_id, key_id "
                "and private_key (Base64-encoded DER EC P-256 private key)."
            ) from None
