# Oak validation record

Validation separates source-level checks, real local operations, model calls and
Telegram delivery. A working adapter alone does not establish that every provider
or deployed conversation has been verified end to end.

## Completed checks

- The latest integrated test suite passed all 56 tests.
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
