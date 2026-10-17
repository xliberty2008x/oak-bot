# Oak UX behavior specification

Status: proposed behavior for the [implementation roadmap](ux-roadmap.md),
reconciled against `6517c71` on 2026-10-18. This specification does not assert that
planned features exist. The current UI and privacy contract is
[DESIGN.md](../DESIGN.md); update it alongside any implemented contract change.

## Shared requirements

- Scope every read/action to the authenticated owner and selected conversation,
  including private Telegram topics. A topic ID or callback request ID alone is
  not authority. Group-chat topics remain unsupported.
- Keep durable intake, decisions, jobs and outgoing delivery receipts. UI retries
  do not replay uncertain external actions. Explicitly rejected, undispatched
  operations and ambiguous dispatches remain distinct.
- Product messages use Ukrainian and Oak terminology. Preserve existing Markdown
  rendering, UTF-16 chunk limits, button sizing, focus and reduced-motion rules.
- Status surfaces show allowlisted activity labels and lifecycle metadata. Tool
  arguments/results, filesystem paths, URLs with credentials and private website
  values never become progress labels, panel summaries or diagnostic copy.
- Reuse existing native controls, A2UI catalogue and private website renderer.
  `InputRequests` handles ordinary questions; website credentials stay in their
  separate private channel. Do not enable `sendDataModel` or add a new form engine.
- Browser listeners/pollers are bound to the current session epoch; abort on
  reset/switch/logout, ignore late results and preserve active drafts/focus.

## UX-01: readable schedule

`/tasks` returns exactly `Розклад порожній.` when `Scheduler.list(scope)` is empty.
Otherwise show jobs in the scheduler's due-time order. Each item includes:

- A whitespace-normalized prompt preview of at most 80 characters, with an
  ellipsis when truncated. Render it as ordinary safe text through the existing
  output renderer; never construct raw HTML from the prompt.
- Local date/time with numeric UTC offset and the configured IANA timezone.
  Convert from the stored epoch with `scheduler.timezone`; never use host-local
  time. The offset distinguishes repeated clock times at a DST transition.
  A stored finite epoch outside the platform's datetime range shows
  `Час поза підтримуваним діапазоном` and retains the cancellation ID; one such
  record cannot prevent the rest of the schedule from being displayed.
- Mode (`Нагадування` / `Задача`), status (`Очікує` / `Виконується` /
  `Потребує перевірки`), and recurrence duration when present.
- Full existing job ID and a usable `/cancel ID` instruction. Inline controls
  belong to UX-08b. Listing cannot change due times, status or stored prompts.

Acceptance: empty list; multiple due times; non-default timezone; both sides of
a DST fold; long/multiline/markup prompts; recurring and uncertain jobs; another
conversation's jobs excluded; out-of-range epoch; unchanged database after listing. Existing schedule
claim/cancel/recurrence/recovery tests remain applicable.

## UX-02: command discovery and onboarding

Register supported commands with Ukrainian descriptions using an explicit chat
scope for each configured owner, after bot-identity/webhook checks. Keep commands
and help in one inventory so `/stop` and `/new`, which have special gateway
routing, remain discoverable. Include `/web` when supported. A registration
failure produces a sanitized diagnostic without breaking durable intake.

`/help` groups conversation, memory, schedule, media and service commands.
`/start` introduces text/photo/file/voice input and exposes the existing Oak
Mini App link when available. Never advertise unimplemented `/forget` or `/model`.
Test exact scopes, command limits, partial owner failure and unavailable Mini App.

## UX-03 and UX-04: approval lifecycle

Keep the existing pending/answered/expired/cancelled authority. Persist a bounded
projection of decision outcome and the Telegram message receipt needed to settle
the card. A callback or `/approve`/`/deny` resolves the original request once;
then the originating card removes controls and shows `Дозволено`, `Відхилено`,
`Прострочено` or `Скасовано`. Callback acknowledgement includes a short result.
Repeated/stale/foreign decisions cannot change the original answer.

Decision success must not depend on a cosmetic edit succeeding. If delivery is
late, its card must reflect the stored terminal state rather than recreate live
buttons. A restart must not restore authority to an old approval. Never repeat
an ambiguous initial send to obtain a message ID.

Add `approval_timeout_seconds`, default 300, integer range 60–3600, for legacy
`Interactions` approvals. Show the deadline in the configured timezone. Send at
most one reminder 60 seconds before expiry while the request is still pending.
Expiry remains a decline and emits one explicit owner notice. Cancellation and
turn completion cancel the timer/reminder. Ordinary `InputRequests` and private
website leases retain their own authority/lifetime; this setting does not extend
them. Document the option in configuration and portable examples.

Acceptance: allow/deny/command/expiry/cancel/restart; double clicks; callback
arrival at expiry; decision before initial card delivery; failed card edit;
wrong owner/topic; no pending waiter/timer leak and no duplicate native response.
Use injected/fake time instead of real five-minute waits.

## UX-05: progress and stop

Track activity by conversation, run ID and tool call ID. Map known tool families
to fixed labels such as `Шукаю в інтернеті`, `Працюю з файлами`,
`Керую комп’ютером`; unknown tools use `Виконую дію`. Never echo arbitrary
`progress.message`, step names or tool names. Show elapsed time after 30 seconds.
Do not invent a total-step count or percentage without authoritative events.
In the current mapper, `TOOL_CALL_END` closes argument assembly and occurs before
execution; end activity on the matching step/result or terminal run event.
Known unsupported-input adapters may show their fixed actionable explanation;
arbitrary progress payloads remain excluded.

The existing reply remains editable at most once per chat per second, with
unchanged status suppressed. Define a separate status row/card if answer text is
already streaming, so status never overwrites model text. Persist enough state
for terminal cleanup and backward-compatible loading of older reply records.
On completion/error/interruption, remove active controls and finish queued text.

Stop callbacks identify the displayed run and use existing owner/topic checks.
Interrupt only if that run is still current; an old button reports completion
instead of stopping a newer run. `requested`, `idle`, `not_active` and uncertain
transport results remain distinct; do not announce completion on acknowledgement.

Acceptance: parallel tools, out-of-order ends, unknown labels, streaming text,
rate limiting, stop/start race, restart with old records, final edit failure,
private-data canaries and no new-send fallback after an ambiguous send.

## UX-06: intake feedback and edits

Queue an optional `👀` reaction only after owner authorization and durable intake
insertion. Correlate terminal `👍` with the actual accepted input/run; steer
acceptance alone is not completion. Cosmetic reaction failures cannot fail or
replay the input. Preserve topic/message identity and deduplication.
The Bot API standard-reaction allowlist supports `👍`, while `✅` is not a
standard reaction; use the supported symbol for completion feedback.
[Transport contract](https://core.telegram.org/bots/api#reactiontypeemoji).
Completion feedback describes processing state; it cannot establish that the
user's task succeeded (`metadata.taskOutcome` remains independently unverified).
Use `👍` only after the bound run completes without a failure/interruption or
uncertain processing result; those outcomes must not receive it.

Default edit policy: consume an authorized `edited_message` durably without
dispatching another model input, command or attachment download. Send one short
notice explaining that a correction should be a new message. Deduplicate by
update ID; later edits must not bypass the owner/topic checks. This avoids
unexpected command execution from a corrected historical message. A later
explicit steer-on-edit feature would require original-message/run linkage.

Acceptance: denied owner, duplicate update, restart, download/submit failure,
failed/interrupted/uncertain completion, command edit, media-caption edit, topic
isolation and edit during an active run.

## UX-07 and UX-08: reminder input and actions

Preserve `/remind 60 текст`. Add a bounded grammar: `10m`, `2h`, `1d`, `18:30`,
`завтра 09:00`, plus ISO date/time. Reject NaN/infinity, negative durations, empty
prompts and unknown syntax with supported examples. Bare clock time means its
next occurrence in the configured timezone; `завтра` means the next calendar
date, not 24 hours. Reject ambiguous/nonexistent DST wall times with instructions
to supply ISO time with an explicit offset. Confirm the resolved date/time.

Reminder-only controls: `+10 хв`, `+1 год`, `Готово`, bound to a stored occurrence
and owner/conversation. Snooze schedules one reminder from click time, with a
durable action receipt; a repeated callback returns the first outcome. It must
not shift an unrelated recurring occurrence or execute an agent `mode=run` job.
Task cancel controls use existing scoped cancellation and show the result.
Failure notices include the bounded task label and ID and preserve uncertain state.

Acceptance: fixed-time grammar/calendar/DST tests; duplicate callbacks; restart
between consumption and delivery; stale occurrence; other owner/topic; recurrence;
agent jobs cannot be snoozed into automatic re-execution.

## UX-09 through UX-11: status and live panel

`/status` reads current model, effort, Turbo selection, active/last-known run
state, pending decisions, nearest pending job and memory count for the selected
conversation. `ControlPanel.summary` already contains some of these values;
Turbo comes from `service_tier_for` and the next job from the jobs query. Build a
small shared read projection if needed; avoid coupling controller commands to
HTTP gateway construction. Do not create a thread or query model providers.

For UX-10, poll the existing redacted `/api/panel` snapshot every five seconds
while visible, allowing only one outstanding request. Pause while hidden and
refresh immediately on return; use bounded backoff on failure and show stale
state. Abort/ignore results from a previous session epoch. Refresh summary and
pending-request metadata without replacing open forms or active drafts. A later
SSE optimization needs an equivalent redacted projection; do not render raw
`/api/events` or `/api/stream` payloads for this feature.

For UX-11, add a bounded legacy-approval projection with ID, safe summary,
expiry and state. Validate owner, selected conversation, kind and pending state
again at decision time, reusing the existing `/api/decision`/resolve path. Plain
ordinary questions continue through `/api/requests/{id}`. Private website forms
continue in their isolated iframe. Exclude request metadata, commands containing
secrets and private values from general summaries/events.

Acceptance: idle/running/uncertain status; multiple topics; request completion in
the other client; expiry during decision; slow response across session switch;
hidden/reopened page; failure recovery; stable form draft/focus; owner isolation.

## Later behavior contracts

| Items | Bounded behavior and acceptance |
| --- | --- |
| UX-12: memory | Search/list/add/delete use `MemoryStore` and the selected scope. `/forget ID` rejects absent/foreign IDs. Panel uses bounded pagination and explicit delete result; no memory in status/events/logs. Test cross-topic access, search escaping, empty states, duplicate delete and draft preservation. |
| UX-13: tasks in panel | Introduce an explicit owner-facing preview of at most 80 normalized characters. Update `DESIGN.md` and the privacy assertions in `test_web.py` with exact allowed projection; raw prompt stays absent from summary/events. Later create/reschedule/pause APIs each get a separate PR, owned operation identity and stale/uncertain handling. |
| UX-14: recovery | Inspection/acknowledgement records that the owner reviewed a record; it does not relabel an unknown external result as success. Manual retry requires explicit confirmation and creates a fresh input/action identity; preserve the original evidence. Test repeated clicks, restart, concurrent run, deleted topics and all uncertain delivery types. |
| UX-15: model | Use catalogue-backed opaque choices and existing idle-only `set_model` rules, effort validation and Turbo compatibility. Reject stale choices and preserve current selection on failure. No billing/authentication fallback or silent default-model change. |
| UX-16: media | Album members remain durably identifiable; collection has a bounded window and count, with explicit behavior for late members and restart. Voice transcript echo is opt-in, bounded and uses the existing transcription once. Test mixed captions, download failures, scopes and duplicate updates. |
| UX-17: Telegram bridge | Apply Telegram theme parameters through existing tokens with readable fallback colours. BackButton closes the current dialog/section before leaving; listeners are cleaned up. Reuse safe-area CSS, add native insets where supported and feature-detect haptics/confirm. Test browser fallback, light/dark, reduced motion, portrait/landscape and real mobile keyboard separately. |
| UX-18: files/activity/settings | Paginated artifact list reuses owner-scoped download authorization and object-URL cleanup. Activity stores bounded run duration/outcome/tool labels; no conversation/tool contents. Reuse existing desktop and lifecycle timelines. Move technical settings into an accessible Diagnostics section. Test stale ownership, removed artifacts, pagination and late fetch cleanup. |
| UX-19: localization | Extract strings incrementally into shared catalogue keys; Ukrainian default, documented locale fallback. Keep dynamic user text separate. Test missing keys and formatter parity without replacing existing rendering. |

## Verification and rollback

Record the exact source SHA, commands, counts and failures in each PR. Reuse the
repository's `unittest` tests and existing Node/browser fixtures where relevant.
Test state transitions and user-visible outcomes, especially races and ownership;
avoid tests that merely duplicate string-building code.

For affected UI changes, record applicable DESIGN.md acceptance rows: incremental
updates, focus/validation, theme, mobile keyboard, cancel/reopen, replay/recovery,
unknown components/limits and private isolation. Mark unrelated or untested rows
explicitly. Mocked API calls do not establish live Telegram or provider support.

Use additive, backward-compatible durable state. Reverting a presentation change
must not delete jobs, decision receipts or intake/outbox history. Before a live
poller is started, verify bot identity and webhook ownership through the existing
startup procedure. Deployments and merges have their own recorded outcomes.
