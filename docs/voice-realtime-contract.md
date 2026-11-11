# Oak Realtime SIP adapter contract

## Scope and isolation

`oak.voice_realtime.RealtimeSipClient` is a callable, opt-in HTTP adapter for
accepting, rejecting and ending an existing incoming Realtime SIP call. It is not
an outbound dialer, webhook server, speech bridge, WebSocket client or production
integration. It accepts the incoming OpenAI leg; it does not originate the
provider leg needed for an outbound owner test. It is not registered in Oak's normal runtime or the offline voice
control tools. No API credential is read from the environment automatically.
Oak's subscription-backed ChatGPT authentication remains unchanged. A separately
approved voice deployment must explicitly supply its own credential and budget.

Default construction makes no network requests and all actions fail closed.
Live construction requires `live_enabled=True`, a dedicated API key, and a trusted
`authorize(call_id, action, payload_bytes)` callback returning exactly `True`.
Do not expose any of these switches or credentials as model-supplied arguments.
The callback is an integration boundary, not a built-in approval system.

## Verified provider contract

Checked 2026-11-11 against the official
[Telephony and SIP guide](https://developers.openai.com/api/docs/guides/voice-sip?api=realtime).
The adapter deliberately uses the Realtime portion, not the distinct GPT-Live API:

- Incoming event: `realtime.call.incoming`, provider identifier `data.call_id`.
- Origin: `https://api.openai.com`; POST paths:
  `/v1/realtime/calls/{call_id}/accept`, `/reject`, `/hangup`.
- Acceptance JSON includes `type: realtime`, explicit model, instructions and
  `audio.output.voice`; rejection sends `status_code`; hangup sends no body.
- Bearer authentication is required. A `200` acknowledges the requested control,
  not successful audio, completed conversation or final teardown.
- Sideband uses `wss://api.openai.com/v1/realtime?call_id={call_id}`.
- Provider routing uses `sip:PROJECT_ID@sip.api.openai.com;transport=tls`.
  Signaling and negotiated SRTP media require separate network paths.

SIP-provider compatibility is unverified. Before deployment obtain confirmation of
external SIP URI routing, TLS signaling, SRTP negotiation, codecs and firewall
requirements for the actual purchased trunk. Do not assume a business phone
number or PBX subscription includes those capabilities. No provider configuration
or live access was tested.

The [Realtime reject reference](https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/reject)
specifies an optional integer status and default 603. The adapter locally restricts
rejections to SIP response classes 3xx–6xx; this is conservative client validation,
not a claim that the Realtime reference specifies those exact bounds.

## Outbound prerequisite

The [provider-neutral outbound GPT-Live adapter](#provider-neutral-outbound-gpt-live-adapter)
below implements a separate HTTP start/hangup control surface. It is not connected
to the offline MCP tools. Before using it, confirm the selected provider's TLS,
Opus and SDES-SRTP support, obtain credential-sharing permission and configure the
trusted authorization, sideband and duration/cost supervision described below.

## Required ingress and authorization integration

Do **not** feed unverified JSON directly into the adapter. Webhook verification
is intentionally delegated to the official SDK rather than implementing custom
cryptography in this dependency-free module.

The official [webhook guide](https://developers.openai.com/api/docs/guides/webhooks)
documents `client.webhooks.unwrap(raw_body, headers, secret=webhook_secret)`.
`verify_incoming_webhook` calls this SDK method using an injected official client,
requires original bytes (maximum 256 KiB), rejects missing/duplicate verification
headers and unexpected event types, and returns only call/event/delivery IDs.
Its tests mock the SDK boundary, not actual cryptographic verification.
Use original bytes and the configured endpoint signing secret; reject signature,
timestamp, missing-header and parsing failures. This SDK operation verifies and
parses. JSON parsing alone does not verify anything.

A future receiver must implement this sequence:

1. Enforce HTTPS, a small body limit, request timeout and a POST-only route.
2. Call the official SDK's `webhooks.unwrap` with the original body and headers.
   Pin and test that SDK in the separate voice deployment. It is not currently
   installed or added to Oak's requirements by this change.
3. Require the expected event type and a valid provider `data.call_id`.
   SIP `From`, `To`, and `Call-ID` headers are untrusted metadata, not owner
   identity or authorization. Provider call and event IDs remain unchanged opaque strings (nonempty, UTF-8
   encodable, at most 4096 bytes). Before insertion into request paths, IDs are
   percent-encoded exactly once as single segments, including literal percent
   signs and dot-only segments; no trimming or Unicode normalization occurs.
4. Durably deduplicate webhook deliveries and maintain a single decision owner
   per call. Store minimal operational identifiers; do not retain raw payloads,
   phone numbers, transcripts, prompts or credentials in logs.
5. Obtain trusted call/action approval, including permitted purpose, session
   configuration, data sharing and voice budget. Persist a pending action intent
   before network activity. `authorize` must atomically claim that exact call ID,
   action and serialized payload. A model cannot approve its own call action.
6. Invoke the matching adapter method. Persist acknowledged or uncertain outcome
   outside this stateless adapter. Block concurrent or repeated acceptance/reject
   decisions and duplicate hangup requests using the durable call ledger.
7. Attach an independently implemented sideband handler after acceptance for
   call state and tools. Give each side effect exactly one owner. Do not enable
   remote MCP tools or private-memory forwarding merely because SIP connects.

Official webhook verification does not prove caller identity. Conversely,
accepting an inbound call is a real action and may start billable voice activity.
Do not use the SDK guide's unconditional auto-accept example as an approval policy.

## API shape

```python
from oak.voice_realtime import RealtimeSipClient

client = RealtimeSipClient()  # Safe default; controls disabled.

# Only a separately approved deployment should construct the live instance:
# client = RealtimeSipClient(
#     api_key=dedicated_voice_secret,
#     live_enabled=True,
#     authorize=durable_authorized_intent_claim,
#     timeout=10.0,
# )
# client.accept(verified_call_id, model=approved_voice_model,
#               instructions=approved_ukrainian_prompt, voice="marin")
# client.reject(verified_call_id, status_code=486)
# client.hangup(verified_call_id)
```

Model selection is explicit: this adapter never changes Oak's normal model.
The prompt should disclose the AI assistant and use Ukrainian; test pronunciation,
interruptions, silence, noisy lines and handoff behavior before relying on it.
No conversational or acoustic quality claim follows from HTTP unit tests.

## Failure and timeout semantics

There is one standard-library HTTPS request per operation, with a fixed host,
TLS verification, no proxy environment support, redirects or automatic retries.
The timeout is a positive finite socket-operation timeout, capped at 30 seconds;
it is **not** a hard whole-operation deadline (DNS and multi-read header processing
can exceed it). An external supervisor must enforce a strict wall-clock deadline
if required, treating cancellation after dispatch as uncertain.

Provider bodies are not read or returned. Errors expose only a generic message,
an optional numeric HTTP status, and `outcome_uncertain`. Any transport failure
or non-200 result after authorization is conservatively uncertain. Never replay
such requests on timeout, process restart, webhook redelivery or a model retry.
Reconcile provider/sideband state and the durable intent first. Closing a socket
or returning an error does not undo an accepted call.

## Verification

Run `python -m unittest discover -s tests -p test_voice_realtime.py -v`.
All tests mock HTTPS and use dummy credentials. They cover exact payloads and
routes, disabled mode, call/action authorization, opaque-ID path encoding and header injection, timeout
bounds, redaction, no retry, no response-body reads and connection closure.
These are protocol unit checks, not live provider, SIP or voice tests.


## Provider-neutral outbound GPT-Live adapter

`oak.voice_live.LiveSipClient` adds opt-in `start(...) -> session_id` and
`hangup(session_id, intent_id=...)` methods. No deployment, live calls, credentials,
subscription fallback or automatic MCP registration are included.

Verified separately on 2026-11-11:

- [Create session reference](https://developers.openai.com/api/reference/resources/live/methods/create):
  POST `/v1/live/sessions`, with `session` and SIP `transport` objects. The trunk
  needs TLS, Opus and SDES-SRTP. Destination and caller number are E.164.
  Digest username/password are transmitted to OpenAI for the selected trunk.
- The reference specifies **201 Created**, whereas the SIP guide currently says
  **200 OK**. The adapter accepts either only with a valid SIP creation response.
  It returns the unchanged session ID, never an inferred “answered” status.
- [Hangup reference](https://developers.openai.com/api/reference/resources/live/subresources/sessions/methods/hangup):
  POST `/v1/live/sessions/{session_id}/hangup`, without a body.

No HTTP status endpoint is invented. Live progress requires a separate sideband
consumer; the existing offline status tool is not provider status.

### Approval contract

The trusted service supplies `api_key`, `live_enabled=True` and `authorize(action)`.
`action` is an immutable `LiveAction` with a local intent ID, operation, destination,
caller number, model, provider endpoint, exact serialized payload SHA-256 and
prompt SHA-256. Hangup instead includes the specific session ID. The callback never
receives the trunk password, username or prompt text. Phone numbers and hashes are
still private; do not log or publish this object. Its representation omits fields.

Approval must cover the intended recipient, the caller ID, purpose, exact prompt,
voice configuration, model, data/credential disclosure to OpenAI and the selected
SIP provider, expected cost limit and duration supervision. Compare fingerprints
against the approved configuration, not merely a caller-supplied “approved” flag.
Bind credential identity separately in the trusted deployment configuration. A
fingerprint is an integrity binding, not a password-storage or anonymization scheme.

Before returning exactly `True`, the callback must durably claim the intent ID,
refusing reuse, concurrent dispatch or a previously uncertain outcome. Neither
adapter contains a durable approval ledger. Do not replace the callback with an
unconditional lambda in deployment. No automatic retry or idempotency guarantee
exists at this API: a second create request can place a second phone call.

The adapter explicitly sets `store=False` and client delegation. It does not
implement that delegation consumer, recording, forwarding private memory, tool
execution or spending limits. A production service must supply the approved
sideband lifecycle and watchdog before enabling origination. The offline prototype's
`max_duration_seconds` is not passed to this API and does not stop a live call.

### Validation and response handling

Only a strict `sips:host[:port][;transport=tcp]` provider URI is accepted. The local
validator excludes credentials, paths, query parameters, unsupported parameters,
local hostnames and non-public literal IPs. It deliberately supports a conservative
subset of hostnames; it does not perform DNS resolution or certify provider routing.
Provider endpoint checks and account restrictions still apply server-side.

The response is read with a 16 KiB limit, decoded as UTF-8 and parsed with duplicate
keys and non-finite constants rejected. It must identify SIP transport and an opaque session ID (nonempty, UTF-8 encodable, at most 4096 bytes).
Only the ID is returned; extra response data is discarded. Invalid/truncated/oversized
responses are uncertain, even after a successful HTTP code: a real call may exist.
Errors and redirects are not followed, retried, or read for messages. Transport
exceptions are sanitized. Socket timeout limitations from above also apply here.

Run `python -m unittest discover -s tests -p test_voice_live.py -v` for the outbound
mock tests. They validate wire payloads, approval fingerprint binding, input
rejection, bounded responses, failure redaction, no retry and the 200/201 discrepancy.
No real SIP, provider enrollment, model conversation, audio or billable API behavior
has been verified. Provider choice remains open; this adapter is provider-neutral.

Local outbound intent IDs retain a separate restricted application grammar. They
are not provider session IDs; signed webhook call/event IDs use the opaque contract.
