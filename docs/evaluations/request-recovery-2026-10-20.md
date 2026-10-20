# Media-heavy conversation resume failure

An existing conversation could no longer accept any input after a gateway
restart. `thread/resume` returned the complete native history, including old
media/tool payloads. A response exceeded the gateway's 16 MiB line limit:
the pending resume failed, Telegram reported unconfirmed input acceptance,
and the disconnected runtime caused the gateway supervisor to restart.
Startup then repeated the restart warning for every historical uncertain input.

The gateway now resumes the same native thread with `excludeTurns: true`.
This omits history from the response, without deleting it from the native
thread. Tool-version migrations request only the latest 12 summary turns;
typed file references come from bounded, conversation-scoped delivery-tool
receipts. Completion-race checks request turn metadata only and require the
exact previous turn to be terminal before dispatching a replacement.

Recovery warnings have durable receipts for the interrupted inputs/turns.
Old uncertain work stays quarantined and is never automatically replayed.
The transport records only static failure categories and drains unread stdout
on shutdown so a failed oversized response cannot strand its subprocess pipes.

## Evidence

The installed subscription runtime's own generated protocol schema confirms
the metadata-only resume and summary/metadata pagination fields. A read-only
comparison against the affected conversation (approximately 146 MB of stored
history), without starting any owner task, reproduced the old behavior:

| Resume response | Result | Elapsed |
| --- | --- | ---: |
| Complete history | `ValueError` from the line limit, then disconnected RPC | 9.737 s |
| `excludeTurns: true` | Same thread resumed; subsequent RPC succeeded | 2.427 s |

The successful connection's largest frame was 2,073 bytes. A separate real
history-page check returned 12 summary turns in 79,829 bytes and retained
owner `localImage` references; 12 metadata-only turns occupied 3,362 bytes.
These calls did not execute or replay the owner's inputs.

Regression coverage exercises persisted-thread input acceptance, oversized
frame cleanup, private-safe error categories, repeated recovery, failed notice
delivery, conversation isolation, bounded migration references and exact-turn
completion checks. A native model round trip and installed-service observation
are separate deployment checks, not implied by these protocol probes.

## Installed verification

The installed Controller subsequently resumed the affected thread with its
existing history and completed a further RPC in 9.327 seconds under concurrent
test load. No owner input was replayed or model turn started in that conversation.
The normal subscription-backed smoke check on separate test context passed
streaming, persisted same-thread recall, steering and interruption. This is real
model execution; it is not a claim that a new child Telegram message was sent.

Deployment verification checked 214 protected files, all 10 execution-boundary
checks, HTTP readiness and unchanged configuration, schedules, memory, Harness
state and uncertain work. The running gateway remained at zero automatic
restarts during verification.

All 64 focused controller/recovery/event/transport tests passed. A subsequent
54-test executor/transport run also passed after updating the synthetic exited
process fixture to supply an EOF stdout stream. The broad discovery run was
stopped during expensive unrelated synthetic-media rendering; it is not recorded
as a full-suite pass.
