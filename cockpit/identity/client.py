

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Optional

from .canonical import CONTRACT_VERSION, b64url_encode, build_signing_string
from .keys import SigningKey

def make_nonce() -> str:

    return b64url_encode(os.urandom(16))

def build_signed_request(
    signing_key: SigningKey,
    *,
    principal: str,
    verb: str,
    args: Optional[Any] = None,
    funding: str = "member-subsidized",
    contract: str = CONTRACT_VERSION,
    ts: Optional[int] = None,
    nonce: Optional[str] = None,
    id: Optional[str] = None,
) -> dict:

    args = {} if args is None else args
    envelope = {
        "contract": contract,
        "type": "req",
        "id": id or str(uuid.uuid4()),
        "ts": ts if ts is not None else int(time.time() * 1000),
        "nonce": nonce or make_nonce(),
        "principal": principal,
        "key_id": signing_key.key_id(),
        "funding": funding,
        "verb": verb,
        "args": args,
    }
    signing_string = build_signing_string(
        type=envelope["type"],
        id=envelope["id"],
        ts=envelope["ts"],
        nonce=envelope["nonce"],
        principal=envelope["principal"],
        key_id=envelope["key_id"],
        funding=envelope["funding"],
        verb=envelope["verb"],
        args=envelope["args"],
        contract=contract,
    )
    envelope["sig"] = signing_key.sign_b64url(signing_string)
    return envelope

def build_enroll_request_envelope(
    signing_key: SigningKey,
    *,
    principal: str,
    device_label: str,
    role: str = "member",
    funding: str = "member-subsidized",
    contract: str = CONTRACT_VERSION,
    ts: Optional[int] = None,
    nonce: Optional[str] = None,
    id: Optional[str] = None,
):

    from .enrollment import EnrollRequest

    args = {
        "pubkey": signing_key.pubkey_b64url(),
        "device_label": device_label,
        "principal": principal,
        "role": role,
    }
    envelope = build_signed_request(
        signing_key,
        principal=principal,
        verb="enroll.request",
        args=args,
        funding=funding,
        contract=contract,
        ts=ts,
        nonce=nonce,
        id=id,
    )

    enroll_req = EnrollRequest(
        pubkey=signing_key.pubkey_b64url(),
        device_label=device_label,
        principal=principal,
        role=role,
        envelope=dict(envelope),
        sig=envelope["sig"],
    )
    return envelope, enroll_req
