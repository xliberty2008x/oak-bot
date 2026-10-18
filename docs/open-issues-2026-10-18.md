# Issue resolution and deployment ledger

The engineering work for the three issues found open on 2026-10-18 is complete
and deployed. This ledger separates implementation, actual checks and external
acceptance that still requires the owner's account, device or original media.
Historical issue descriptions did not authorize account sign-in, purchases or
publication of private attachments.

Source baseline: `378866a2445777d3fc7f7dad31a2dc610d77d1e1`.
Deployed application: `24cc3776ce71fa0df0bf0d51ec07d5b09923371a`.
The final ledger update is documentation only and does not change that deployed
application identity.

| Issue | Result | Status |
| --- | --- | --- |
| [#1: universal event/rendering harness](https://github.com/xliberty2008x/oak-bot/issues/1) | Independent ordered conversation workers and an owner-scoped, read-only message view using the shared safe Markdown AST. | Implemented, tested and deployed; physical-client limits below. |
| [#24: free image animation](https://github.com/xliberty2008x/oak-bot/issues/24) | Anonymous Hub discovery, local procedural eye/hair animation, immutable source bindings and mandatory independent candidate review. | Implemented, tested and deployed; synthetic media evidence only. |
| [#64: private CLI OAuth input](https://github.com/xliberty2008x/oak-bot/issues/64) | Broker-owned Colab process, private one-use human input and status-only task/session integration, served from the immutable release. | Implemented, tested and deployed; real account authentication not performed. |

## Merged changes

All changes used bounded PRs, focused checks, exact-tree publication and squash
merges into current main. Existing durable intake, jobs, approvals, outbox and
uncertain-delivery records retain their no-replay rules.

| Area | Merged PRs |
| --- | --- |
| Initial reconciliation and acceptance boundaries | [#78](https://github.com/xliberty2008x/oak-bot/pull/78) |
| Independent event workers, message projection and browser renderer | [#79](https://github.com/xliberty2008x/oak-bot/pull/79), [#84](https://github.com/xliberty2008x/oak-bot/pull/84), [#92](https://github.com/xliberty2008x/oak-bot/pull/92) |
| Public Space discovery, local animation and tool/worker integration | [#80](https://github.com/xliberty2008x/oak-bot/pull/80), [#85](https://github.com/xliberty2008x/oak-bot/pull/85), [#88](https://github.com/xliberty2008x/oak-bot/pull/88), [#97](https://github.com/xliberty2008x/oak-bot/pull/97), [#98](https://github.com/xliberty2008x/oak-bot/pull/98) |
| Durable review requirements across turns, restart, copy and rename | [#86](https://github.com/xliberty2008x/oak-bot/pull/86), [#91](https://github.com/xliberty2008x/oak-bot/pull/91) |
| Private CLI contracts, process, broker and cleanup | [#81](https://github.com/xliberty2008x/oak-bot/pull/81), [#82](https://github.com/xliberty2008x/oak-bot/pull/82), [#83](https://github.com/xliberty2008x/oak-bot/pull/83), [#90](https://github.com/xliberty2008x/oak-bot/pull/90) |
| Pinned official Colab adapter and immutable provider validation | [#87](https://github.com/xliberty2008x/oak-bot/pull/87), [#89](https://github.com/xliberty2008x/oak-bot/pull/89), [#93](https://github.com/xliberty2008x/oak-bot/pull/93), [#99](https://github.com/xliberty2008x/oak-bot/pull/99) |
| Private panel bridge and standalone CLI service | [#94](https://github.com/xliberty2008x/oak-bot/pull/94), [#95](https://github.com/xliberty2008x/oak-bot/pull/95) |
| Persistent reciprocal broker runtime masks | [#101](https://github.com/xliberty2008x/oak-bot/pull/101) |
| Complete build identity and asynchronous verification cleanup | [#96](https://github.com/xliberty2008x/oak-bot/pull/96), [#100](https://github.com/xliberty2008x/oak-bot/pull/100) |

The message view excludes tool payloads and private forms. It displays deltas as
literal text, then replaces them with the authoritative final snapshot. Safe
DOM construction, bounded history, explicit truncation, keyboard-scrollable
tables and session/runtime-epoch guards preserve channel and owner boundaries.

The free animation path is procedural 2D. The agent prepares selected motion
regions and a matching authored closed-eye pose. Complete open/closed poses
avoid the clipped contours found in the first candidate; intermediate eyelid
shapes are not inferred. Each candidate remains subject to the original brief
and an independent review before `oak_send_file`. See
[local animation](local-animation.md).

The CLI broker creates its own approved process and private cache. A human
starts it through a separate-origin UI; code input is consumed once and never
exposed through ordinary forms, model tools, durable events or parent messages.
Native PTYs are not adopted and X11 authorization is not bypassed. The pinned
official Colab adapter verifies provider metadata with a read-only account
request; token/cache contents remain broker-private. See
[private CLI operation and provisioning](private-cli.md).

## Validation evidence

- Full application suite at `7e46622097efc2bd0de66040fd852df4b40518f2`:
  **1,397 discovered, 1,146 executed, 251 skipped, no failures or errors**.
  The final deployed commit changes only the provider sealing script,
  synthetic verification script and their tests relative to this suite.
  Their focused checks passed: two real-filesystem sealing tests and 25
  bootstrap feature tests.
- Final bootstrap verification on deployed-source code: **89 protocol tests,
  zero failures/errors/skips**, plus actual Chromium ordinary submit/cancel and
  synthetic login/OTP/cancel. Four native responses, no new turns, both
  cleanup receipts confirmed. A controlled three-second cleanup delay verifies
  the bounded polling fix; uncertain or missing cleanup still fails.
  This synthetic check does not claim real credential isolation or Google login.
- Three actual Chromium message-view cases and a Node check covered literal
  deltas, safe AST finalization, hostile URLs/HTML, table keyboard access,
  session changes and stale responses. Desktop/light and mobile/dark visual
  fixtures had no page errors, external media loads or horizontal overflow.
- Actual private CLI process/Chromium fixtures covered one-use input,
  foreign/stale scope, cancellation, cleanup and secret canaries. Equal-length
  password entries produced identical screenshots; canary values were absent
  from model-visible events, parent messages, browser local/session storage,
  inspected SQLite records and captured logs.
  Five tests exercised the installed pinned official OAuth/cache contract
  with network calls mocked; no real account exchange occurred.
- Real FFmpeg encoding/decoding and focused animation/source-binding tests
  passed. The first independent native review requested revision for clipped
  eye contours. After the authored-pose repair, a second actual
  subscription-backed `gpt-6.1-sol` review passed against the unchanged brief.
  It assessed 15 evidence images selected from 72 decoded frames, not
  continuous playback of every frame. The reviewed video was an explicitly
  synthetic illustration with SHA-256
  `4eb6d032df31eabe599b8ff1d7f91ae14daa7c348517eb91b9aa6df2168b30b3`.
  No original child attachment was available or recreated.

Feature code digest:
`ef91dfa6d1afcb46795766a167346f30e6b8c65c0d530a1278d31862eaefd7bc`.

## Verified rollout

The gateway was idle before stopping. A fresh private profile/configuration
backup was made, the existing execution-boundary installer applied the reviewed
immutable release, and current-boot attestation passed. The worker release
identifier is `f06545da5390c3dac61f`; its policy digest is
`795efeff8c37fd46de53dc33adb1be9d6423f2a0784ec98d02cc96841e1b9a9d`.
Private CLI paths remain outside executor mounts. The two human-input services
have reciprocal inaccessible paths; the model executor uses a distinct UID.

The first post-start probe exited unsuccessfully and automatically restored the
previous code/configuration. Rollback health was verified; no old action
database was restored. Safe stage diagnostics were added to the local probe.
A second controlled rollout of the same application commit passed every probe.
The first probe's exact failing operation was not retained and is not inferred.

After the HTTP checks passed, independent mount-namespace probes found one
remaining packaging defect: the CLI could open the website broker's runtime
directory. Its optional mask had been skipped while that directory was absent.
The paired recipe in PR #101 creates both runtime roots with tmpfiles, preserves
them across stops and requires both reciprocal runtime masks. Actual transient
systemd fixtures verified both directions, preserved inodes while each peer
restarted, and refused startup with a missing mandatory root. The live units
were repaired with a private configuration backup and no application or action
database change. Each live peer was stopped/started while the other stayed
running; all three CLI-to-website and four website-to-CLI directory probes
denied access before and after those cycles. Gateway readiness and the full
read-only deployment probes passed again.

Final live evidence:

- Gateway and worker running, phase `running`, zero restarts; subscription auth
  and `gpt-6.1-sol` preserved.
- Public static assets returned 200; unauthenticated bootstrap/panel returned
  401. Eight authenticated owner endpoints, including messages, returned 200.
  The current feature manifest and runtime epoch matched.
- The public `messages.js` bytes matched the deployed checkout. Private CLI
  HTTPS returned 200 with `no-store` and the expected parent CSP.
- Peer-checked CLI health reported ready without starting authentication.
  A real `ExecutionClient` request reached the isolated creative worker and
  observed the public Space's declared image-to-video schema. Generation
  access, cost, quota and queue remain unknown.
- Read-only Telegram checks confirmed the configured bot, empty webhook,
  21 owner commands and Mini App menu. No test messages were sent.
- Active turns, running jobs and pending intake/delivery work remained zero.
  All five pre-existing uncertain-intake records were preserved without replay.

## External acceptance limits

- The new message view was exercised in real local Chromium, not on physical
  Telegram/iOS/Android devices. Historical rich-renderer confirmation is not
  new physical-client evidence. Live model token streaming was not revalidated
  in this rollout; `sendRichMessageDraft` remains unimplemented.
- The local procedural path provides a free tool, not a universal generative
  video provider. Naturalness must be judged per candidate. No original child
  MP4 was produced or delivered during this work. Hub discovery bypasses the
  missing external connector method; it does not repair that server. Real
  public Space upload/generation, free quota and queue were not exercised.
- Real Google OAuth requires the owner's action in the private channel and
  was not initiated. Supported GPU inventory is not account-specific allocation
  availability, which remains unknown. No allocation, purchase, paid fallback,
  API-key fallback or external attachment publication occurred.
