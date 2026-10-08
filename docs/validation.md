# Oak validation record

Validation separates source-level checks, real local operations, model calls and
Telegram delivery. A working adapter alone does not establish that every provider
or deployed conversation has been verified end to end.

## Completed checks

- An earlier integrated validation passed all 71 tests. New feature checks and
  environment-specific failures are recorded in dated sections below.
- The control panel passed browser-key authentication, four-view navigation,
  task lifecycle details, cancellation confirmation and computer permission
  changes through its real HTTP endpoints with isolated synthetic state.
  Mobile (390 pixels) and desktop (1440 pixels) had no horizontal page overflow
  or uncaught JavaScript errors. The owner approved the displayed mobile design.
  These checks do not establish a live Telegram launch or provider OAuth success.
- Subscription authentication and availability of the exact `gpt-6.1-sol` model.
- Real model streaming, same-session continuation, active-turn steering and
  cancellation using isolated synthetic prompts.
- Live private Telegram input produced completed model turns in an earlier local
  deployment, followed by restart with the same persisted state.
- The current deployment generated and delivered real PNG, MP4 and WAV artifacts
  through Telegram; successful delivery receipts were recorded. A real reminder
  also reached the owner conversation.
- Native subscription image generation produced a real image. A resumed runtime
  session retained its registered dynamic tools. A harmless connected-app Figma
  identity read completed successfully; app listing also reported Canva available.
  Listing is not evidence of tested Canva operations or external writes.
- Selected private historical notes and voice-conversation text were imported
  into deployment memory. No source identities, transcripts or credentials are
  included in this repository.
- Focused standard-library checks for authentication, event mapping, input
  deduplication, allowlists, output chunking and controller race handling.
  Session-open failures are persisted as failed or uncertain and are not replayed
  automatically on recovery.
- Real local Pillow PNG generation with Ukrainian text and FFmpeg MP4 montage
  generation from two synthetic images.
- Real Piper Ukrainian WAV narration and local Vosk transcription of that
  synthetic audio. The compact recognizer made some word errors.
- Real isolated Playwright open, read, type, click and screenshot operations
  against a synthetic local page.
- Public page reading, Bing RSS search and public YouTube metadata retrieval
  without account credentials or media downloads.
- Four focused adapter checks for Unicode PNG output, workspace path boundaries,
  executable-content removal and URL validation.
- Voice-model setup: official download endpoints responded successfully; the
  small voice configuration was retrieved and existing local model files passed
  cached installation and Piper checksum validation. Synthetic archive checks
  accepted the expected layout and rejected traversal and symbolic links. Large
  model files were not redundantly downloaded during this check.
- AG-UI conversion passed the authoritative 1.0 JSON Schema for 19 synthetic
  events covering all 13 currently emitted event types, including tool events,
  custom interactions, errors and cancellation. Routing identity survived in
  metadata. The check used an isolated temporary `jsonschema` installation;
  it adds no runtime or test dependency. This verifies emitted shapes, not every
  optional AG-UI feature or lifecycle sequence.
- Focused rendering checks cover shared AST structure, native rich messages,
  labelled classic tables, Unicode, unfinished Markdown, unsafe URLs/HTML,
  parser limits and complete literal fallback. Transport checks cover explicit
  rich rejection versus uncertain delivery, rate limits and typing cleanup.
- A native Telegram table with bordered, striped and compact presentation was
  delivered. The owner confirmed that it looked better.
- The real HTTPS web interface rendered in desktop and mobile layouts. Two
  independent conversations produced separate model sessions and actual text
  deltas. Browser checks also passed with delayed Telegram SDK loading and
  blocked cookies. Detailed evidence remains in private deployment records.
- A real service stop/start preserved sessions. Runtime-home migration retained
  three existing session IDs, their rollout history and 13 registered dynamic
  tools. The service now uses an isolated runtime home to avoid sharing mutable
  session state with Codex Desktop.
- A forced connector-catalog refresh restored app listings after migration.
  This confirms discovery, not every provider operation.
- The owner confirmed that the Mini App opened on both a phone and Telegram for
  Mac through the alternate SSH preview endpoint. The previous Cloudflare
  endpoint was reachable from the server but did not load on the owner's Mac,
  including in Safari. Changing the HTTPS endpoint resolved this observed issue.
- After runtime-home migration, a resumed session invoked the registered local
  image tool and returned a real PNG through the authenticated artifact endpoint.
- Computer use passed a real subscription-backed model task on an isolated X11
  desktop: the model read a screenshot, entered the displayed Ukrainian text and
  clicked the verification button. Six native tool calls returned six image
  results to the model. Additional real desktop checks covered Ukrainian text,
  emoji, newline input, key chords, mouse dragging and scrolling. The configured
  deployment display was checked read-only; computer-use Telegram delivery was
  not part of this test.

## Fresh Ubuntu preparation — 2026-10-06

- Used the official Ubuntu Base 24.04.5 amd64 image, verified against SHA-256
  `e77b6f10c2590cef872b33ee9f635a0e3fd1f57fb074c0e52b5c7f56147a0c86`.
  Isolated mount/PID namespaces and `pivot_root` kept the host filesystem intact;
  only Python, sudo and CA certificates were added before bootstrap.
- Full `--prepare-only` installed the system packages, pinned runtime and Python
  dependencies, Chromium, Piper and Vosk models, and private deployment paths.
  Rerunning preparation passed with the existing configuration preserved.
- The sandboxed browser on the actual 1280×800 virtual desktop passed screenshot,
  mouse and Ukrainian keyboard checks using the distribution's packages.
- All 65 tests passed inside that Ubuntu environment in 1.819 seconds. Real Piper
  synthesis produced a 2.69-second WAV and Vosk returned a nonempty transcript;
  this verifies the local voice pipeline, not recognition accuracy.

No account credentials, bot token or memory were copied into the clean environment.
Fresh-environment subscription login, model calls and live Telegram delivery were
not tested; the existing deployment's checks above are separate evidence. This
was a clean Ubuntu userland using the host kernel, not a newly booted VM. It does
not verify an actual reboot or application of the Chromium AppArmor profile on
an Ubuntu kernel enforcing its user-namespace restriction.

## Private Telegram topic sessions — 2026-10-06

- Existing standard-library tests verify persistent owner/topic routing, legacy
  General state, separate memory/jobs, callback approval scope, topic delivery
  after restart, pending-intake migration and cancellation, and `/web` selection.
- Authenticated HTTP checks verify session/task ownership, selected-topic runtime
  inventory and OAuth parameters, owner confirmation, and no repeated native
  topic creation after an uncertain response or changed operation identifier.
- The real control-panel frontend passed isolated browser checks at 390 and 1440
  pixels: five views, topic selection, task details/cancel/stop, creation/rename,
  stale-response rejection, and safe rendering of a topic name containing HTML.
  Runtime and Telegram transports used synthetic responses in those checks.
- Read-only live `getMe` confirmed private topics and user topic management are
  enabled by the owner. After rollout, the owner created a real topic through
  the Mini App and confirmed Oak replied inside that topic. Read-only state
  checks found a separate native model session, one completed turn and one
  delivered terminal reply. Existing General state survived the restart.
- Live browser checks at both widths passed all five views and separate
  General/topic scopes. Owner endpoints returned 200, anonymous sessions 401,
  with no JavaScript errors or page overflow; cookie-free Bearer refresh passed.
  Cross-topic live approval and a two-topic memory experiment were not tested.

## Manual remote desktop — 2026-10-06

- The existing suite covers owner/session-bound remote tickets, WebSocket origin
  checks, replay and expiry, takeover gating, input leases and key validation.
- An isolated Xvfb desktop with a real Chromium browser and noVNC connection
  passed mouse input, physical Ukrainian text, common keyboard shortcuts and
  ordered text/Backspace/Enter. Cookie-free Bearer authentication also passed.
- Browser checks at 390×844 and 844×390 passed relative touchpad movement, tap,
  right-click, two-finger scrolling and drag. Screen, pad and Stop stayed visible.
- Stop and network loss released held pointer buttons, ended the bridge, cleared
  the viewer and restored navigation. Input created no screenshot artifacts.
- These are operator checks on synthetic content. Native Telegram device
  keyboards, desktop IME, local clipboard integration and provider sign-in are
  not established by these results.

## A2UI у наявному Mini App — 2026-10-08, локальний Mac

- Окремий checkout почато з `303cb892a1478e6157776f6c3be03f7801bb7fe7`,
  branch `feature/a2ui-miniapp`. Production source/config/state не змінювались.
- Усі 14 нових стандартних unittest для A2UI пройшли: native tool, form/action,
  incremental result, text fallback, receipts/repeated requests, stale sessions,
  cancellation, malformed batches, ownership, signed synthetic Telegram
  initData, origin guard та crash/recovery без автоматичного replay.
- Повна suite виконала 94 тести: 91 пройшов, один failure та два errors.
  Чистий detached checkout початкового commit виконав 80 тестів і мав рівно
  ті самі три збої. Два існуючі Telegram-тести залежать від різниці Mac paths
  `/var`/`/private/var`; тест title card потребує Linux DejaVu Sans path.
  Це не green full suite і не нові A2UI-регресії.
- Offline validator з `jsonschema==4.25.1` перевірив 8 server messages і
  1 client action проти незмінених офіційних A2UI v0.9.1 schemas та власного
  каталогу Oak. Три fixtures з unsupported version/catalog/component відхилено.
- `node --check` для `app.js` і `a2ui.js`, Python compile та `git diff --check`
  використані як статичні перевірки; окремий lint/type-check tool у цьому
  vanilla-JS/standard-library проєкті не налаштований.
- Ізольований Chromium відкрив справжні frontend/HTTP endpoints local demo.
  Перевірені form choices → submit → incremental result → edit, повторні
  натискання, збереження чернетки при навігації, stale HTTP rejection,
  malformed renderer snapshots та cancellation. Synthetic network rejections
  перевіряють renderer recovery; серверне відхилення malformed/stale data
  окремо покрите unittest.
- Мобільні 390×844, 844×390 та desktop 1440×1000 перевірені без
  горизонтального overflow чи uncaught JavaScript errors. Картка використовує
  наявні Living Control Center classes/components. Локальні screenshots/report
  у `output/playwright/` ігноруються Git і не містять real credentials.

Demo використовує synthetic agent runtime й synthetic signed Telegram launch;
це не реальний model tool call, Telegram-клієнт або delivery. VM revision,
live A2UI round-trip і deployed Mini App цією feature task не перевірені.
Deploy, restart, migration, push, bot-menu changes та external messages не
виконувались. Rollout і live verification описані в [A2UI](a2ui.md).

## Remaining deployment checks

- Browser artifact download is not yet verified: the last browser check had no
  existing artifact to download.
- The cron autostart entry is installed, but startup after an actual machine
  reboot has not been tested.
- Live approval round-trips and behavior across additional Telegram clients
  still need their own verification. Rerun the suite after integration changes.

Additional connected-app operations depend on account/provider access and need
their own live task. A configured local voice pipeline does not
establish multilingual accuracy. Selected memory import requires actual exported
content; unavailable server-side memory has not been reconstructed or assumed
empty.

The runtime streams actual assistant text deltas; it is not merely a completed
response wrapped in one event. Telegram renders coalesced sends and edits, while
web uses SSE or polling of the same durable events. `sendRichMessageDraft` is not
implemented or claimed as verified. Group topics and every optional AG-UI event
family are outside the current private-conversation interface.

## Reproduce

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m oak doctor --config config.local.json
.venv/bin/python -m oak smoke --config config.local.json
```

The smoke command uses the existing subscription login and makes real model
calls. It does not contact Telegram or use private deployment context. Browser,
voice and video checks require their configured optional binaries and models.
