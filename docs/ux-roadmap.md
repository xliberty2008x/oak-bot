# Oak UX implementation roadmap

Status: proposed implementation sequence. Source baseline: `main` at `6517c71`,
rechecked on 2026-10-18. Product behavior and acceptance criteria are in
[ux-spec.md](ux-spec.md). Read [AGENTS.md](../AGENTS.md) and
[DESIGN.md](../DESIGN.md) before implementing an item.

The goal is to make ongoing work visible, make owner decisions understandable,
and let owners manage their conversation without copying internal identifiers.
Deliver one bounded behavior per PR. The first PR is this plan and specification;
runtime changes follow in separate PRs. A proposed item is not a deployment or a
claim of live Telegram verification.

## Reconciliation of the original audit

The original static audit used `299005b`. Do not implement its proposals without
checking the current source: native input requests, A2UI, private website forms,
browser transfers and remote desktop have since changed substantially.

| Audit item | State at `6517c71` | Consequence for implementation |
| --- | --- | --- |
| B1: `/tasks` JSON and empty state | Open: `Controller.command` serializes `Scheduler.list`; `"[]"` is truthy. | UX-01 fixes presentation only. |
| B2: edits become another input | Open: Telegram accepts `edited_message` through ordinary intake. | UX-06 defines an explicit edit policy before changing routing. |
| B3: approval buttons remain | Open: legacy approval callbacks do not settle their originating message. | UX-03 adds feedback and durable message linkage. |
| B4: silent approval expiry | Open: `Interactions.ask` uses a fixed 300-second timeout. | UX-04 makes expiry visible and configurable. |
| B5: topics documentation | Correction: `docs/design.md` says **group** topics are unsupported; private-chat topics exist and group chats are rejected. | UX-00 clarifies this distinction; do not claim group-topic support. |
| T1: work visibility | Open: the event mapper emits tool/step/progress events; Telegram drops tool/step events and has no handler for `CUSTOM progress`. | UX-05 uses a bounded presentation of activity and adds a run-bound stop action. |
| T2: command menu | Open: startup sets the Mini App menu button, but does not register commands. | UX-02 registers owner-scoped commands and groups help. |
| T3: questions and MCP forms | Partly implemented: `Interactions.user_input` delegates to `InputRequests.native`; supported native questions and MCP schemas open a contextual Mini App form through `requests.js`. Legacy approval handling remains separate. | Reuse `InputRequests`; no second JSON form engine or credential input through Telegram replies. |
| T4: reminders | Open: `/remind` accepts seconds, notifications have no snooze controls, failure notices identify jobs by ID. | UX-07 and UX-08 extend input and controls separately. |
| T5: status, memory, model | Open: `/status` is minimal, `/forget` and chat model selection are absent. | UX-09, UX-12 and UX-15 reuse existing stores/model validation. |
| T6: intake feedback and albums | Open: no reactions or media-group coalescing. Voice transcription is used internally. | UX-06 and UX-16 preserve durable intake and original attachment identity. |
| T7/M5: uncertain actions | Partly implemented: durable uncertain state, counts and contextual-request delivery attention exist; there is no general reconciliation/retry workflow. | UX-14 distinguishes inspection from explicit new execution. |
| M1: live panel | Open: sessions refresh every 15 seconds; selected-panel state has no equivalent ongoing refresh. | UX-10 refreshes the redacted panel snapshot; raw AG-UI streams contain content and are unsuitable for status-only rendering. |
| M2: owner decisions in panel | Partly implemented: pending contextual requests can be opened and answered; legacy approvals still direct the owner to Telegram. | UX-11 bridges legacy approvals without replacing the request lifecycle. |
| M3: memory | Open: panel shows a count; `MemoryStore` already implements search/remember/forget. | UX-12 adds conversation-scoped management. |
| M4: task labels | Open, but intentional: `ControlPanel.tasks` omits prompts and `test_web.py` asserts this privacy contract. | UX-13 introduces an explicit bounded owner-facing label and updates the contract/tests together. |
| M6: Telegram integration | Partly implemented: safe-area CSS already exists; theme is fixed dark and native back/haptic integration is absent. | UX-17 extends the existing layout and Telegram bridge. |
| M7: files/activity/settings | Partly implemented: artifact download, task lifecycle timelines and live noVNC desktop already exist. | UX-18 adds discovery/history; do not rebuild desktop access. |
| Cross-cutting localization/tool labels | Open: product strings remain distributed and no shared activity-label vocabulary exists. | Activity labels start in UX-05; broader localization is UX-19. |

Source entry points: [controller.py](../oak/controller.py),
[telegram.py](../oak/telegram.py), [interaction.py](../oak/interaction.py),
[requests.py](../oak/requests.py), [schedule.py](../oak/schedule.py),
[panel.py](../oak/panel.py), [web.py](../oak/web.py),
[events.py](../oak/events.py), [app.js](../oak/web/app.js),
[requests.js](../oak/web/requests.js), [style.css](../oak/web/style.css).

## Small PR sequence

Each row is a separate review unit. Split a row further if its implementation
requires a new state transition or a distinct API contract. Independent items
may proceed in parallel after UX-00; dependencies describe merge order.

| ID | Priority / scope | Depends on | Main files / acceptance |
| --- | --- | --- | --- |
| UX-00 | Plan, specification, private-topic documentation | — | These docs; verify links and source claims. |
| UX-01 | Readable `/tasks`, including a real empty state | UX-00 | `controller.py`; localized date/time, preview, mode/status, recurrence and full cancellation ID; no job mutation. |
| UX-02 | Telegram command discovery and grouped help/onboarding | UX-00 | `telegram.py`, `__main__.py`, `controller.py`; owner-scoped menu, actual supported commands only, startup remains usable if registration fails. |
| UX-03 | Approval decision feedback and button cleanup | UX-00 | `interaction.py`, `telegram.py`; decision resolves once, origin message settles for callback and command decisions. |
| UX-04 | Approval expiry and one pre-expiry reminder | UX-03 | `interaction.py`, configuration docs; expiry remains a decline, cancellation/restart remain distinct. |
| UX-05a | Telegram tool activity and elapsed time | UX-00 | `events.py`, `telegram.py`; allowlisted labels, bounded edits, recovery and final-answer behavior. |
| UX-05b | Stop button bound to the displayed run | UX-05a | `telegram.py`, `controller.py`; stale controls cannot interrupt a newer run. |
| UX-06a | Durable intake acknowledgement reactions | UX-00 | `telegram.py`; acknowledge only accepted durable input; cosmetic failures never replay input. |
| UX-06b | Explicit behavior for edited messages | UX-00 | `telegram.py`; ignore edits with one explanatory notice; new messages remain the way to send corrections. |
| UX-07 | Human reminder time syntax | UX-01 | `controller.py`, `schedule.py`; bounded grammar, configured timezone, explicit DST handling. |
| UX-08a | Reminder snooze/done actions | UX-07 | `schedule.py`, `telegram.py`; owned reminder occurrence, duplicate-safe snooze, no implicit rerun of agent jobs. |
| UX-08b | Task cancellation controls and identifiable failure notices | UX-01 | Existing command/outbox path; scoped cancel and bounded job label, no automatic retry. |
| UX-09 | Useful `/status` | UX-00 | Controller read model; model/effort/Turbo/activity/waiting/next job/memory counts, without initializing a thread or querying a provider. |
| UX-10 | Automatic selected-panel refresh | UX-00 | `app.js`; cancellable polling of the existing redacted panel API, scope/visibility handling, drafts preserved. |
| UX-11 | Legacy approvals in Mini App | UX-03, UX-10 | `panel.py`, `web.py`, `app.js`; bounded approval projection and existing decision path, stale/foreign rejection. |
| UX-12a | `/forget` with explicit scope and result | UX-00 | `controller.py`, `memory.py`; selected conversation only; missing IDs do not delete anything. |
| UX-12b | Mini App memory list/search/add/delete | UX-12a | Scoped API plus existing panel components; bounded pagination and delete confirmation. |
| UX-13a | Task display labels in Mini App | UX-00 | Explicit API/design-contract change; at most 80 characters in the owner-selected conversation, never raw prompt in summary/events. |
| UX-13b | Create/reschedule/pause tasks | UX-13a, UX-07 | One mutation per PR; durable operation receipts and explicit uncertain handling. |
| UX-14a | Uncertain-input inspection and acknowledgement | UX-00 | Controller/panel API; acknowledge review separately from execution outcome. |
| UX-14b | Explicit manual retry | UX-14a | New owned input/action identity after owner confirmation; preserve original uncertain record. |
| UX-15 | Chat model selection | UX-02 | Existing `model_catalog`/`set_model`; apply current idle-only and Turbo compatibility rules. |
| UX-16a | Telegram album intake | UX-06a | Durable media-group membership, bounded collection window, restart and partial-download policy. |
| UX-16b | Optional voice transcript echo | UX-00 | Configurable, bounded owner-visible quote; transcribe once and preserve input scope. |
| UX-17a | Telegram light/dark theme | UX-10 | Existing CSS tokens and theme-change listener; test both themes. |
| UX-17b | Telegram navigation, safe-area bridge and haptics | UX-17a | BackButton/modal history, existing safe-area CSS, optional native confirmation with browser fallback. |
| UX-18a | Artifact list/gallery | UX-00 | Owner-scoped list over existing download authorization, pagination and object-URL cleanup. |
| UX-18b | Activity history and diagnostic settings | UX-10, UX-05a | Lifecycle/tool-label metadata only; reuse existing desktop and task timeline. |
| UX-19 | Localization catalogue | UX-02, UX-05a | Extract existing strings incrementally; Ukrainian remains default and examples stay portable. |

`sendMessageDraft` is a later capability experiment, not required for this
roadmap. Establish current Bot API support and preserve uncertain-send behavior
before proposing it. Unsupported MCP schema types remain an explicit decline
unless a separately reviewed extension to the existing catalogue is needed.
Direct Telegram answer buttons/ForceReply are deferred; this sequence retains
the existing contextual Mini App input and its native request binding.

## Handoff and progress ledger

| Item | State at this documentation change | PR / evidence |
| --- | --- | --- |
| UX-00 | In review | [PR #25](https://github.com/xliberty2008x/oak-bot/pull/25); source reconciliation against `6517c71`, 36 local links and diff/portable-path checks passed. |
| UX-01 | In review | [PR #26](https://github.com/xliberty2008x/oak-bot/pull/26), head `d772c95`; 9 command, 14 state and 11 renderer tests passed; no live Telegram/deployment check. |
| UX-02 through UX-19 | Planned | No completion, deployment or live verification claimed. |

Next implementation: UX-02 command discovery, or independently UX-03 approval
feedback. Review UX-00 before merging UX-01; both PRs target `main` independently.

For every implementation PR, update this ledger with its URL and actual check
results. Use `prepared`, `in review`, `merged`, and `deployed/verified` separately;
never infer deployment from a merge. Record source SHA and remaining limitations.

To continue after a handoff:

1. Fetch `origin/main`; inspect current PRs and this ledger to avoid duplicate work.
2. Recheck the relevant audit row and source contract against that SHA. Preserve
   concurrent fixes; a source baseline is evidence, not a version to restore.
3. Read the matching specification section and existing tests. Implement only
   that row on its own branch; keep production/account state outside Git.
4. Run focused standard-library tests and the applicable design acceptance rows.
   Distinguish fixtures, actual browser checks, model runs and live Telegram.
5. Open a small PR with validation and limitations, and link it in the ledger.
   Keep schema additions backward compatible so reverting a UI change does not
   erase durable inputs, jobs, decisions or delivery receipts.

Useful focused commands, run from a checkout with its dependencies installed:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_state.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_telegram.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_requests.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_web.py' -v
git diff --check
```

Run only suites relevant to the change, plus checks required by the repository.
The old audit's test results do not establish a baseline for this source version.
Documentation-only PRs need source/link/diff checks; application tests and live
services are unnecessary for them.
