
from __future__ import annotations
import json, time, uuid

MEDIA_OFFER = "media.offer"
MEDIA_ANSWER = "media.answer"
MEDIA_CANDIDATE = "media.candidate"
MEDIA_READY = "media.ready"
MEDIA_UPDATE = "media.update"
MEDIA_BYE = "media.bye"
MEDIA_ERROR = "media.error"

ALL_TYPES = frozenset((MEDIA_OFFER, MEDIA_ANSWER, MEDIA_CANDIDATE, MEDIA_READY,
                       MEDIA_UPDATE, MEDIA_BYE, MEDIA_ERROR))

BUS_EVENT_KIND = "media"

CH_VOICE = "voice"
CH_VIDEO = "video"
CH_SCREEN_BOX = "screen-box"
CH_SCREEN_USER = "screen-user"
ALL_CHANNELS = frozenset((CH_VOICE, CH_VIDEO, CH_SCREEN_BOX, CH_SCREEN_USER))

SENDRECV = "sendrecv"
SENDONLY = "sendonly"
RECVONLY = "recvonly"
INACTIVE = "inactive"
DIRECTIONS = frozenset((SENDRECV, SENDONLY, RECVONLY, INACTIVE))

_MIRROR = {SENDRECV: SENDRECV, INACTIVE: INACTIVE, SENDONLY: RECVONLY, RECVONLY: SENDONLY}

def mirror_direction(d: str) -> str:
    return _MIRROR.get(d, INACTIVE)

def new_session_id() -> str:

    return "ms_" + uuid.uuid4().hex

def channel(name: str, direction: str = SENDRECV) -> dict:

    if name not in ALL_CHANNELS:
        raise ValueError(f"unknown channel {name!r}")
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    return {"channel": name, "direction": direction}

def _envelope(t: str, session_id: str, **fields) -> dict:
    e = {"t": t, "session_id": session_id, "ts": time.time()}
    e.update({k: v for k, v in fields.items() if v is not None})
    return e

def offer(session_id, sdp, channels, ice_servers, *, role="box"):

    return _envelope(MEDIA_OFFER, session_id, sdp=sdp, channels=channels,
                     ice_servers=ice_servers, role=role)

def answer(session_id, sdp, *, accepted_channels=None):
    return _envelope(MEDIA_ANSWER, session_id, sdp=sdp, accepted_channels=accepted_channels)

def candidate(session_id, cand, *, seq=0, mid=None, mline_index=None, end=False):

    return _envelope(MEDIA_CANDIDATE, session_id, candidate=cand, seq=seq,
                     sdpMid=mid, sdpMLineIndex=mline_index, end=end or None)

def ready(session_id, *, who="box"):
    return _envelope(MEDIA_READY, session_id, who=who)

def update(session_id, channels):

    return _envelope(MEDIA_UPDATE, session_id, channels=channels)

def bye(session_id, *, channel_name=None, reason=None):

    return _envelope(MEDIA_BYE, session_id, channel=channel_name, reason=reason)

def error(session_id, code, message):
    return _envelope(MEDIA_ERROR, session_id, code=code, message=message)

def dumps(env: dict) -> bytes:
    return json.dumps(env, separators=(",", ":")).encode()

def loads(raw) -> dict:
    return json.loads(raw if isinstance(raw, (str, bytes, bytearray)) else json.dumps(raw))

def validate(env: dict) -> str | None:

    t = env.get("t")
    if t not in ALL_TYPES:
        return f"unknown signaling type {t!r}"
    if not isinstance(env.get("session_id"), str) or not env["session_id"]:
        return "missing session_id"
    if t == MEDIA_OFFER:
        if not isinstance(env.get("sdp"), str) or not env["sdp"]:
            return "offer missing sdp"
        chs = env.get("channels")
        if not isinstance(chs, list) or not chs:
            return "offer missing channels"
        for c in chs:
            if not isinstance(c, dict) or c.get("channel") not in ALL_CHANNELS:
                return f"offer has an invalid channel {c!r}"
            if c.get("direction", SENDRECV) not in DIRECTIONS:
                return f"offer channel has invalid direction {c!r}"
        if not isinstance(env.get("ice_servers"), list):
            return "offer missing ice_servers"
    elif t == MEDIA_ANSWER:
        if not isinstance(env.get("sdp"), str) or not env["sdp"]:
            return "answer missing sdp"
    elif t == MEDIA_CANDIDATE:
        if not env.get("end") and not isinstance(env.get("candidate"), (str, dict)):
            return "candidate missing candidate (and not end-of-candidates)"
    elif t == MEDIA_UPDATE:
        chs = env.get("channels")
        if not isinstance(chs, list) or not chs:
            return "update missing channels"
    return None
