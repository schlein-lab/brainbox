

from __future__ import annotations

from . import errors
from .canonical import (
    CONTRACT_VERSION,
    args_digest_hex,
    b64url_decode,
    b64url_encode,
    build_signing_string,
    jcs_canonical_bytes,
    key_id_from_pubkey,
)
from .client import (
    build_enroll_request_envelope,
    build_signed_request,
    make_nonce,
)
from .config import FederationPolicy, IdentityConfig
from .enrollment import EnrollmentManager, EnrollRequest
from .errors import AuthError
from .keys import (
    SigningKey,
    load_public_from_b64url,
    verify_detached,
    verify_detached_b64url,
)
from .registry import (
    KeyRecord,
    KeyRegistry,
    ROLE_GUEST,
    ROLE_MEMBER,
    ROLE_OWNER,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REVOKED,
)
from .verifier import NonceCache, RequestVerifier, VerifiedRequest

__all__ = [

    "CONTRACT_VERSION",
    "build_signing_string",
    "jcs_canonical_bytes",
    "args_digest_hex",
    "key_id_from_pubkey",
    "b64url_encode",
    "b64url_decode",

    "SigningKey",
    "load_public_from_b64url",
    "verify_detached",
    "verify_detached_b64url",

    "KeyRegistry",
    "KeyRecord",
    "ROLE_OWNER",
    "ROLE_MEMBER",
    "ROLE_GUEST",
    "STATUS_PENDING",
    "STATUS_APPROVED",
    "STATUS_REVOKED",

    "IdentityConfig",
    "FederationPolicy",

    "EnrollmentManager",
    "EnrollRequest",

    "RequestVerifier",
    "VerifiedRequest",
    "NonceCache",

    "build_signed_request",
    "build_enroll_request_envelope",
    "make_nonce",

    "errors",
    "AuthError",
]

__version__ = "0.1.0"
