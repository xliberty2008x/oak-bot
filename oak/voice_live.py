"""Isolated, disabled-by-default GPT-Live BYO SIP origination and hangup.

No runtime wiring, credential discovery, HTTP status polling, or automatic retry.
See docs/voice-realtime-contract.md before integrating this billable transport.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
import re

from oak.voice_realtime import RealtimeControlError, RealtimeSipClient, validate_call_id, encode_provider_id


@dataclass(frozen=True, repr=False)
class LiveAction:
    """Trusted approval input. Contains personal numbers; do not log this object.

    Hashes bind approval to exact bytes without exposing credentials or prompts.
    Hashes of secret-containing payloads must also remain private.
    """
    intent_id: str
    action: str
    payload_sha256: str
    destination: str = ""
    caller_number: str = ""
    model: str = ""
    provider_url: str = ""
    instructions_sha256: str = ""
    session_id: str = ""


def _intent_id(value: str) -> str:
    # Local application intent keys are not opaque provider identifiers.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise ValueError("Invalid local intent identifier")
    return value


def _text(value: str, label: str, limit: int) -> str:
    try:
        valid = (isinstance(value, str) and bool(value.strip())
                 and len(value.encode("utf-8")) <= limit
                 and not any(c in value for c in "\r\n\0"))
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError("Invalid " + label)
    return value


def _number(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\+[1-9][0-9]{1,14}", value):
        raise ValueError("Phone number must use E.164 format")
    return value


def validate_provider_url(value: str) -> str:
    """Validate a strict public TLS SIP endpoint, without DNS or network reads."""
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("Invalid SIP provider endpoint")
    match = re.fullmatch(r"sips:(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?(?:;transport=tcp)?", value)
    if not match or (match[2] and not 1 <= int(match[2]) <= 65535):
        raise ValueError("Invalid SIP provider endpoint")
    host = match[1].strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.lower().split(".")
        if (len(labels) < 2 or labels[-1] in {"local", "localhost", "internal", "lan", "home", "invalid", "test"}
                or not re.fullmatch(r"[a-z]{2,63}", labels[-1])
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
            raise ValueError("SIP provider must use a public endpoint")
    else:
        if not address.is_global or address.is_multicast:
            raise ValueError("SIP provider must use a public endpoint")
    return value


def _reject_nonfinite(_):
    raise ValueError("Non-finite JSON value")


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


class LiveSipClient:
    """Caller supplies dedicated voice key, live opt-in and trusted durable policy.

    authorize(LiveAction) must atomically claim a unique durable intent and return
    exactly True after owner approval of material details, credential transmission
    to OpenAI/provider, prompt and budget. Refuse spent/in-flight/uncertain intents.
    The callback must NOT be model-provided. This adapter is deliberately stateless.
    """
    MAX_RESPONSE_BYTES = 16384

    def __init__(self, *, api_key: str = "", live_enabled: bool = False,
                 authorize: Callable[[LiveAction], bool] | None = None,
                 timeout: float = 10.0):
        # Reuse configuration validation, never its Realtime request paths.
        RealtimeSipClient(api_key=api_key, live_enabled=live_enabled, timeout=timeout)
        self._api_key = api_key
        self._enabled = live_enabled
        self._authorize = authorize
        self._timeout = timeout

    def start(self, *, intent_id: str, destination: str, caller_number: str,
              provider_url: str, username: str, password: str, model: str,
              instructions: str, voice: str = "marin") -> str:
        """Return initialized session ID, NOT proof the callee answered."""
        intent_id = _intent_id(intent_id)
        destination, caller_number = _number(destination), _number(caller_number)
        provider_url = validate_provider_url(provider_url)
        username = _text(username, "SIP username", 256)
        password = _text(password, "SIP password", 4096)
        model, voice = _text(model, "model", 128), _text(voice, "voice", 64)
        # Instructions may contain newlines; reject malformed Unicode/empty/huge input.
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 16000:
            raise ValueError("Invalid voice instructions")
        try:
            prompt_hash = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
            body = json.dumps({
                "session": {"model": model, "instructions": instructions,
                            "audio": {"output": {"voice": voice}},
                            "delegation": {"type": "client"}, "store": False},
                "transport": {"type": "sip", "destination": destination,
                              "trunk": {"provider_url": provider_url,
                                        "auth": {"type": "digest", "username": username, "password": password},
                                        "caller_number": caller_number}},
            }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid voice instructions") from None
        action = LiveAction(intent_id, "start", hashlib.sha256(body).hexdigest(),
                            destination, caller_number, model, provider_url, prompt_hash)
        return self._post("/v1/live/sessions", body, action, create=True)

    def hangup(self, session_id: str, *, intent_id: str) -> None:
        session_id = validate_call_id(session_id)
        action = LiveAction(_intent_id(intent_id), "hangup", hashlib.sha256(b"").hexdigest(),
                            session_id=session_id)
        self._post("/v1/live/sessions/" + encode_provider_id(session_id) + "/hangup", None, action, create=False)

    def _post(self, path: str, body: bytes | None, action: LiveAction, *, create: bool):
        if not self._enabled:
            raise RealtimeControlError("Live voice controls are disabled")
        if not self._api_key:
            raise RealtimeControlError("Dedicated voice API credential is required")
        if self._authorize is None:
            raise RealtimeControlError("Call action is not authorized")
        try:
            permitted = self._authorize(action)
        except Exception:
            raise RealtimeControlError("Call authorization failed") from None
        if permitted is not True:
            raise RealtimeControlError("Call action is not authorized")
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.openai.com", timeout=self._timeout)
            headers = {"Authorization": "Bearer " + self._api_key}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            # Reference says 201 for creation; current SIP guide says 200.
            if response.status not in ((200, 201) if create else (200,)):
                raise RealtimeControlError("Voice provider did not acknowledge the action",
                                           status_code=response.status, outcome_uncertain=True)
            if not create:
                return None
            raw = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise ValueError("Oversized response")
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json,
                                parse_constant=_reject_nonfinite)
            if not isinstance(result, dict) or result.get("transport", {}).get("type") != "sip":
                raise ValueError("Unexpected transport")
            session_id = validate_call_id(result["session"]["id"])
            return session_id
        except RealtimeControlError:
            raise
        except Exception:
            raise RealtimeControlError("Voice control failed; reconcile before retrying",
                                       outcome_uncertain=True) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
