# Ukrainian SIP voice tools: first prototype

This is an **opt-in development component**, not an enabled Oak calling feature.
It prepares the control boundary for a provider-neutral SIP trunk (a Ukrainian number is the
initial goal) and a separately configured OpenAI voice backend. It does not change
Oak's subscription-backed model, speech broker or paid-fallback rules.

## Implemented

- `oak.voice_mcp`: a standalone MCP stdio process, usable by an MCP client now.
  It has no HTTP listener and does not start with the regular Oak service.
- `oak.voice_control`: durable offline call fixtures, scoped to one conversation.
  Preparation is idempotent. A consumed simulation cannot replay. Interrupted
  simulations become uncertain after restart; cancellation does not erase that
  uncertainty.
- `oak.voice_realtime`: a real, separately opt-in HTTPS adapter for OpenAI
  Realtime SIP accept/reject/hangup operations. It builds and sends the documented
  requests when an embedding service explicitly enables it and authorizes the
  exact action. It is **not exposed through the offline MCP tools**. See the
  [provider contract](voice-realtime-contract.md) for sources and limitations.
- `oak.voice_live`: provider-neutral outbound SIP creation and hangup through
  the documented GPT-Live API, also disabled by default and not registered as
  an agent tool. It accepts a compatible, separately provisioned trunk rather
  than embedding carrier settings. Its required TLS, Opus and SDES-SRTP support
  must be confirmed with the selected operator.

The MCP fixtures require no credentials and spend no money. The HTTPS adapter's
tests substitute its transport; no provider operation has been performed.

## Tool contract

| Tool | Behavior |
| --- | --- |
| `voice_capabilities` | Report offline status and missing live integration gates. |
| `voice_prepare_call` | Save a fixture with an opaque destination alias, idempotency key and intended duration. |
| `voice_simulate_call` | Consume one mock outcome: connected, completed, failed or uncertain. Never dial or start voice. |
| `voice_call_status` | Read only the selected conversation's fixture. |
| `voice_cancel_call` | Cancel a prepared fixture or end a simulated connection. Not a real SIP hangup. |

All results explicitly identify `offline_simulation`. Duration is a validated
planning limit (15–600 seconds), **not a running media timer** in this prototype.
A successful fixture says nothing about provider access, codecs, audio quality,
latency, speech recognition or Ukrainian voice quality.

There is intentionally no `voice_start_live_call` tool and no model-supplied
`approved`, `scope`, `live` or credential argument. Unknown arguments are rejected.

## Run locally, without accounts

Python 3.11+ on Linux is sufficient. No new package installation is required for
the fixture MCP server or the standard-library HTTPS adapter.

```bash
python3 -m unittest discover -s tests -p 'test_voice_control.py' -v
python3 -m unittest discover -s tests -p 'test_voice_mcp.py' -v
python3 -m unittest discover -s tests -p 'test_voice_realtime.py' -v
python3 -m unittest discover -s tests -p 'test_voice_live.py' -v

# Start one MCP process for a conversation selected by a trusted launcher.
# Choose a private state directory outside version control.
python3 -m oak.voice_mcp \
  --scope development-conversation \
  --state-dir "$HOME/.local/share/oak-voice-prototype"
```

The process reads one JSON-RPC message per stdin line and writes only JSON-RPC
responses to stdout. An MCP client should negotiate initialization and then send
`notifications/initialized` before discovering/calling tools. EOF closes the
process. It never imports the API key from the environment.

Minimal line-oriented exchange (each object is a separate line):

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"local-fixture-client","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"voice_prepare_call","arguments":{"request_key":"test-one","destination_ref":"owner-test","max_duration_seconds":60}}}
```

Use the returned `call_id` for simulation/status/cancellation. Reusing the same
request key and details returns the existing fixture. Reusing the key with
different details is rejected. A new key is a distinct fixture, not a retry.

The transport implements initialization, ping, tool discovery and tool calls
under [MCP stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
It has no resources, prompts, sampling, pagination, HTTP, SSE or remote auth.
Synchronous fixture operations finish locally; asynchronous live execution would
need a different worker/transport design rather than blocking this loop.

## Boundaries before connecting to Oak

1. The trusted gateway chooses a conversation scope and starts its dedicated
   process. Do not use a global shared scope, let the model select one, or reuse
   one server for unrelated conversations. The scope is not a secret or an
   authentication token; possession of process-launch/file access remains trusted.
2. State is separated by scope in private SQLite files with an exclusive process
   lock. Only one service may own a scope; a second owner fails rather than
   mistaking in-progress work for a crash. Directory mode must be 0700 and state
   files 0600. State directories must remain outside Git and untrusted workspaces.
3. Fixtures accept opaque destination aliases, not phone numbers, SIP URLs,
   prompts, history, credentials or recordings. They perform no memory retrieval
   or transcript import. Real caller identity must be authenticated by a trusted
   gateway, never inferred from caller ID alone.
4. Enabling an API client is separate from permission for a call. The embedding
   service must resolve destination aliases privately, check the owner's requested
   recipient/purpose and applicable spending limits, then durably authorize only
   that call. A model-created boolean or arbitrary webhook does not grant this.
   Existing valid owner authorization should be used without redundant prompts.
5. Track accepted, started, ended and uncertain provider states separately.
   Persist an attempt before network I/O. Reconcile ambiguous outcomes instead of
   automatically repeating accepts, origination or hangups. A provider HTTP 200
   is not proof of delivered speech or a completed call.
6. Build teardown/time and spend enforcement before live calling: disconnect on
   either leg ending, duration expiry, owner cancellation, backend failure and
   process loss. The offline planning duration is not such enforcement.

## What to obtain from the SIP provider

The operator must confirm the product actually supplies a usable SIP trunk,
not merely an app-managed virtual-PBX extension:

- Assigned number, caller-ID policy and incoming/outgoing destinations.
- SIP registrar/proxy and whether authentication uses registration, credentials,
  source-IP allowlisting or a combination.
- Supported SIP transports, TLS/SRTP availability, RTP address/port requirements,
  NAT behavior, codecs, packetization and DTMF method.
- Concurrent-call limits, test restrictions, outbound rate/connection fees,
  included minutes and other tariff terms.
- Whether direct routing to an external SIP URI is supported; otherwise the
  required SBC/PBX route, and whether any channel/forwarding leg is charged twice.

Do not paste passwords or account identifiers into issues or PRs. Obtain private
configuration through the deployment's approved secret channel. No registrar,
port, codec, phone number or authentication mode is assumed by this PR.

## Remaining work and acceptance gates

1. Confirm the provisioned SIP trunk and choose direct routing versus an
   SBC/PBX. A Python REST adapter is not a SIP/RTP stack. The Live adapter can
   submit an outbound creation request only after a compatible trunk is supplied;
   Realtime accept controls only an incoming leg.
2. Verify the chosen OpenAI backend/account and its billing contract. Realtime
   API use is separate from ChatGPT subscription authentication. Do not silently
   reinterpret existing subscription-only operation as approval for paid API use.
3. Integrate authenticated/signed events, a durable live action ledger, private
   alias resolution and authorized call controls. Connect the selected outbound
   adapter and media path based on the actual operator contract. Kyivstar is one
   candidate, not a hardcoded dependency. GPT-Live origination sends trunk Digest
   credentials to OpenAI; this credential disclosure requires explicit owner
   authorization before the live configuration is used.
4. Add actual runtime expiry, spend ceilings, idempotent teardown, duplicate and
   out-of-order event handling, restart recovery and webhook replay protection.
5. Run a deliberately authorized, bounded owner-phone test: both audio directions,
   Ukrainian speech, barge-in, silence, packet loss, hangup on either side, failure
   recovery and carrier/provider cost accounting. Record results separately.
6. Only then register live controls in Oak's conversation-scoped gateway and
   document their permissions. No deployment or automatic runtime registration
   is included here.

No private repository material, account state, transcripts or persona data is
included. Tests and examples are synthetic.
