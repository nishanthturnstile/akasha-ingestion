from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest, new
from time import time
from urllib.parse import urlencode

from akasha.config import Settings


@dataclass(frozen=True, slots=True)
class SignedReference:
    resource_id: str
    operation: str
    path_template: str
    expires_at: int
    key_id: str
    signature: str

    def query_string(self) -> str:
        return urlencode(
            {
                "op": self.operation,
                "exp": self.expires_at,
                "kid": self.key_id,
                "sig": self.signature,
            }
        )


class SigningService:
    def __init__(self, settings: Settings) -> None:
        self._secret = settings.signing_secret.get_secret_value().encode()
        self._ttl_seconds = settings.signed_url_ttl_seconds
        self._key_id = "default"

    def sign(
        self,
        *,
        method: str,
        operation: str,
        resource_id: str,
        path_template: str,
        geometry_or_query_hash: str,
    ) -> SignedReference:
        expires_at = int(time()) + self._ttl_seconds
        signature = self._signature(
            method=method,
            operation=operation,
            resource_id=resource_id,
            path_template=path_template,
            expires_at=expires_at,
            geometry_or_query_hash=geometry_or_query_hash,
        )
        return SignedReference(
            resource_id=resource_id,
            operation=operation,
            path_template=path_template,
            expires_at=expires_at,
            key_id=self._key_id,
            signature=signature,
        )

    def verify(
        self,
        *,
        method: str,
        operation: str,
        resource_id: str,
        path_template: str,
        geometry_or_query_hash: str,
        expires_at: int,
        key_id: str,
        signature: str,
    ) -> bool:
        if key_id != self._key_id or expires_at < int(time()):
            return False
        expected = self._signature(
            method=method,
            operation=operation,
            resource_id=resource_id,
            path_template=path_template,
            expires_at=expires_at,
            geometry_or_query_hash=geometry_or_query_hash,
        )
        return compare_digest(expected, signature)

    def query_hash(self, payload: str) -> str:
        return sha256(payload.encode()).hexdigest()

    def _signature(
        self,
        *,
        method: str,
        operation: str,
        resource_id: str,
        path_template: str,
        expires_at: int,
        geometry_or_query_hash: str,
    ) -> str:
        canonical = "\n".join(
            [
                "v1",
                method.upper(),
                operation,
                resource_id,
                path_template,
                str(expires_at),
                geometry_or_query_hash,
            ]
        )
        return new(self._secret, canonical.encode(), sha256).hexdigest()
