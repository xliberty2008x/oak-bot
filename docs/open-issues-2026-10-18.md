# Open issue resolution ledger

Source baseline: `378866a2445777d3fc7f7dad31a2dc610d77d1e1`, checked on
2026-10-18 (Europe/Kyiv). Three GitHub issues are open. This ledger records
existing behavior separately from implementation and external verification.
Issue descriptions are historical evidence, not authority to start account
sign-ins, buy credits or publish private attachments.

| Issue | Current evidence | Bounded work | State |
| --- | --- | --- | --- |
| [#1: universal event/rendering harness](https://github.com/xliberty2008x/oak-bot/issues/1) | Durable scoped EventBus, per-run mapping, deadline typing, native rich/classic fallback and shared safe Markdown AST exist. A held conversation lock can delay unrelated runtime events; the current Mini App has no read-only message renderer. | Independent ordered conversation event workers; bounded owner-scoped assistant-message view, literal deltas and final safe AST DOM. Preserve the redacted status poll and private form channel. | In progress |
| [#24: free image animation](https://github.com/xliberty2008x/oak-bot/issues/24) | Static preview is labelled honestly; browser upload supports hidden file inputs/choosers; video inspection exists. The connected Spaces search method is unavailable. Running public Spaces do not establish free quota or generation access. | Anonymous read-only Hub discovery and schema/capability preflight; a feasible free local animation path with explicit motion regions and truthful limits; tool/worker integration and result inspection. No purchase or paid fallback. | In progress |
| [#64: private CLI OAuth input](https://github.com/xliberty2008x/oak-bot/issues/64) | Native PTY output reaches runtime/AG-UI, its cache is model-readable, and the native sandbox deliberately lacks desktop state. Existing private website leases supply reusable authority rules. | A broker-owned fixed approved process from creation, private stdin/stdout/cache, one-use human-origin UI, task/session binding and status-only verification; explicit unsupported status without capability. Never adopt a native PID/session or bypass X11 authorization. | In progress |

## Delivery sequence

1. Publish this reconciliation and acceptance boundary first.
2. Deliver one bounded behavior per PR. Root integrates named commits into a
   current-main worktree, verifies exact trees before publication and preserves
   concurrent changes. Schema changes retain durable receipts and no-replay rules.
3. Run focused unittest/Node checks, then actual local Chromium or media fixtures
   for affected behavior. Check concurrent scopes, stale authority, cancellation,
   unknown dispatch and secret canaries. Provider availability, simulated CLI
   authentication and real account authentication are different results.
4. Review execution/broker packaging before rollout. New immutable worker code
   requires the existing installer and current-boot attestation procedure;
   source or UI checks alone do not establish isolation.
5. Update this ledger with PRs, exact source/test evidence and deployment outcome.
   Close an issue only for demonstrated resolution; record any external account,
   quota or physical-device verification that remains outstanding.

## Acceptance boundaries

- Browser message rendering is read-only. No new chat composer or credential
  form engine is introduced. Raw tool arguments/results remain outside this
  view, and final message snapshots take precedence over incomplete deltas.
- A local procedural animation must be described as procedural, preserve
  unselected artwork and demonstrate eye/hair movement independently of camera
  motion. It is not a claim of arbitrary generative video or universal quality.
  Inspect the result before delivery; do not recreate an absent child attachment.
- A CLI OAuth code belongs only to its broker process and human channel. Never
  send it through chat, ordinary A2UI/native input, model tool arguments, durable
  events or screenshot-capable model desktop. Exit status alone does not prove
  provider authentication or GPU allocation availability.
- Existing subscription-backed `gpt-6.1-sol`, private account directories and
  owner/topic boundaries are preserved. No billing/authentication fallback,
  speculative GPU allocation or automatic resource purchase is authorized.

## Evidence and progress

Initial focused harness audit: 79 event/rendering/Telegram/web tests passed.
The lock-contention reproduction delayed chat B while chat A held its routing
lock; releasing A allowed B's three events to be emitted. This establishes the
remaining scheduling defect without claiming mixed message contents.

Implementation PRs and final checks will be recorded here as they complete.
