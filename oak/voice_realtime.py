"""Explicitly opt-in Realtime SIP controls, separate from Oak's normal runtime.

This module does not register tools, read credentials, receive webhooks, or dial.
The embedding service must verify webhooks and persist action authorization first.
"""
from __future__ import annotations

import http.client
import json
import math
import re
from collections.abc import Callable


class RealtimeControlError(RuntimeError):
    """Sanitized failure. An uncertain outcome must never be blindly replayed."""

    def __init__(self, message: str, *, outcome_uncertain: bool = False,
                 status_code: int | None = None):
        super().__init__(message)
        self.outcome_uncertain = outcome_uncertain
        self.status_code = status_code


def validate_call_id(call_id: str) -> str:
    # The provider's opaque ID, NOT the caller-controlled SIP Call-ID header.
    if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", call_id):
        raise ValueError("Invalid Realtime call ID")
    return call_id


class RealtimeSipClient:
    """One HTTPS request per authorized operation; no redirects or retries.

    authorize(call_id, action, payload_bytes) must return the boolean True only
    after trusted policy approval. It should atomically claim a durable intent
    tied to these exact bytes, refusing duplicate/in-flight/uncertain intents.
    Never source this callback or live_enabled from model tool arguments.
    """

    def __init__(self, *, api_key: str = "", live_enabled: bool = False,
                 authorize: Callable[[str, str, bytes | None], bool] | None = None,
                 timeout: float = 10.0):
        if not isinstance(live_enabled, bool):
            raise ValueError("live_enabled must be boolean")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 30):
            raise ValueError("Timeout must be greater than zero and at most 30 seconds")
        if not isinstance(api_key, str) or (api_key and (
                not api_key.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in api_key))):
            raise ValueError("Invalid voice API credential")
        self._api_key = api_key
        self._enabled = live_enabled
        self._authorize = authorize
        self._timeout = timeout

    def accept(self, call_id: str, *, model: str, instructions: str,
               voice: str = "marin") -> None:
        """Accept with explicitly selected voice model and Ukrainian prompt.

        A 200 acknowledges setup, not that conversation or media succeeded.
        """
        for value, label, limit in ((model, "model", 128), (voice, "voice", 64),
                                    (instructions, "instructions", 16000)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("Invalid session " + label)
        self._post(call_id, "accept", {
            "type": "realtime", "model": model, "instructions": instructions,
            "audio": {"output": {"voice": voice}},
        })

    def reject(self, call_id: str, *, status_code: int = 603) -> None:
        if type(status_code) is not int or not 300 <= status_code <= 699:
            raise ValueError("SIP rejection status must be an integer from 300 to 699")
        self._post(call_id, "reject", {"status_code": status_code})

    def hangup(self, call_id: str) -> None:
        """Acknowledge teardown request, not proof that media has stopped."""
        self._post(call_id, "hangup", None)

    def _post(self, call_id: str, action: str, payload: dict | None) -> None:
        call_id = validate_call_id(call_id)
        if not self._enabled:
            raise RealtimeControlError("Live voice controls are disabled")
        if not self._api_key:
            raise RealtimeControlError("Dedicated voice API credential is required")
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if self._authorize is None:
            raise RealtimeControlError("Call action is not authorized")
        try:
            permitted = self._authorize(call_id, action, body)
        except Exception:
            raise RealtimeControlError("Call authorization failed") from None
        if permitted is not True:
            raise RealtimeControlError("Call action is not authorized")
        connection = None
        try:
            # Fixed origin and native HTTPS avoid proxy environment injection,
            # redirects, automatic retries, and configurable credential sinks.
            connection = http.client.HTTPSConnection("api.openai.com", timeout=self._timeout)
            headers = {"Authorization": "Bearer " + self._api_key}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request("POST", "/v1/realtime/calls/" + call_id + "/" + action,
                               body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            # Do not read/log provider bodies, which may echo private content.
            if status != 200:
                raise RealtimeControlError("Voice provider did not acknowledge the action",
                                           status_code=status, outcome_uncertain=True)
        except RealtimeControlError:
            raise
        except Exception:
            raise RealtimeControlError("Voice control transport failed; reconcile before retrying",
                                       outcome_uncertain=True) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def verify_incoming_webhook(raw_body: bytes, headers: dict[str, str], *,
                            sdk_client: object, webhook_secret: str) -> dict[str, str]:
    """Verify via an injected official OpenAI SDK client, then extract minimal IDs.

    This helper neither authorizes a call nor deduplicates delivery. Supply a real
    OpenAI client in deployment, never a model-selectable verifier. Its SDK stays
    an optional dependency of the isolated voice service, not Oak's core.
    """
    if type(raw_body) is not bytes or not raw_body or len(raw_body) > 262144:
        raise RealtimeControlError("Invalid webhook body")
    if not isinstance(webhook_secret, str) or not webhook_secret.strip():
        raise RealtimeControlError("Webhook signing secret is required")
    if not isinstance(headers, dict):
        raise RealtimeControlError("Invalid webhook headers")
    normalized = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RealtimeControlError("Invalid webhook headers")
        name = key.lower()
        if name in normalized:
            raise RealtimeControlError("Duplicate webhook headers")
        normalized[name] = value
    for name in ("webhook-id", "webhook-timestamp", "webhook-signature"):
        value = normalized.get(name, "")
        if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise RealtimeControlError("Missing or invalid webhook verification headers")
    try:
        event = sdk_client.webhooks.unwrap(raw_body, normalized, secret=webhook_secret)
        if event.type != "realtime.call.incoming":
            raise ValueError("Unexpected event")
        call_id = validate_call_id(event.data.call_id)
        event_id = event.id
        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", event_id):
            raise ValueError("Invalid event ID")
    except Exception:
        # Includes invalid signature/timestamp, malformed SDK events, and missing
        # SDK functionality. No parsed-JSON or unsigned compatibility fallback.
        raise RealtimeControlError("Webhook verification or event validation failed") from None
    return {"call_id": call_id, "event_id": event_id, "webhook_id": normalized["webhook-id"]}
