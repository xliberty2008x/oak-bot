# Trusted Oak speech client

This change applies to the Oak repository and its managed `oak service` process.
It does not update the separate legacy `telegram-gateway.service` deployment.
The subscription-backed LLM, selected conversation models and Piper TTS remain
unchanged. The optional speech broker owns OpenAI credentials, budgets, provider
selection and local NeMo fallback; see [broker deployment](README.md).

## Configuration and boundaries

`gateway-config.patch.json` is a partial overlay, disabled by default. Merge it
into the current private config only after the broker and provider route have
been validated, then explicitly set `speech_to_text.enabled=true`. Preserve all
other configuration, durable state and account paths.

The Unix socket must be a real socket at its canonical absolute path, owned by
the gateway UID with mode 0600. Parent directories must be safely owned and not
group/world writable, apart from standard sticky temporary ancestors. The
socket must remain outside conversation, workspace, state and runtime roots.
The provisioned `/run/oak-stt/asr.sock` satisfies this boundary when configured
for the actual gateway account. No firewall edits or public endpoint are needed.

The gateway checks `/ready` with a three-second timeout, normalizes audio with
single-threaded FFmpeg, and submits multipart `file`, `language=uk-UA` and
`response_format=json` to `/v1/audio/transcriptions`. Source processing is capped
at 20 MiB and 60 seconds; a 240-second local request deadline permits bounded
primary and fallback attempts. The broker returns text and optional provider
metadata; its errors and full transcripts are not logged by the client.

Automatic voice input and `oak_transcribe` use the same trusted gateway path.
When audio resides in the execution worker, the gateway obtains an existing
bounded `ExecutorFiles` snapshot before recognition. Provider keys and audio
upload code are not sent into the model runtime or worker. The existing
attachment remains available; only new recognition snapshots and temporary WAV
files are removed. Existing immutable executor code/profile need no changes.

`oak_transcribe` now describes the configured recognizer accurately. The tools
version advances through Oak's existing controlled tool upgrade, retaining
conversation selection and the selected LLM rather than silently changing it.

## Validate

Use the deployment's existing Python with its installed dependencies. These
commands do not read provider keys, start a poller or call a paid provider:

```sh
python -m unittest discover -s tests -p test_speech_to_text.py -v
python -m unittest discover -s tests -p test_tools.py -v
python -m unittest discover -s tests -p test_telegram_ux.py -v
python -m unittest discover -s tests -p test_transfer.py -v
python -m unittest discover -s tests -p test_state.py -v
python -m compileall -q oak tests
cd deployment/speech-to-text
python3 -m unittest -v test_server
```

Generated test audio and a private mock Unix HTTP server verify primary/fallback
response handling, real local FFmpeg normalization, wrong/oversized inputs,
safe failures, total timeouts, cancellation during decoder/HTTP/snapshot work,
snapshot reuse and cleanup, owner authorization and duplicate-update handling.
Provider access and recognition quality still require explicitly authorized live
tests. Never claim a configured key or `/ready` proves paid API access.

## Rollout and rollback

Keep the implementation in a separate reviewed branch/worktree. Before changing
production, record the active source commit, create a rollback branch at that
commit, and save the exact private config outside Git in a mode 0700 directory
with a mode 0600 file. Record the original source path and service command.

Do not run a second `oak run` process. Oak's service helper binds its process
identity to its source checkout, so use the **currently active checkout** to
inspect and stop it:

```sh
.venv/bin/python -m oak service status --config /absolute/private/config.json
.venv/bin/python -m oak service stop --config /absolute/private/config.json
```

After authorized pending work has stopped, deploy the reviewed code into the
same active checkout and install the complete updated private config. Keep that
source path stable so existing autostart continues to use it. Start it once:

```sh
.venv/bin/python -m oak service start --config /absolute/private/config.json
.venv/bin/python -m oak service status --config /absolute/private/config.json
```

Verify one gateway worker, bot identity/webhook ownership through the project's
normal startup checks, configured broker readiness and a small authorized voice
test. Check that model/effort, TTS, conversations and schedules remain intact.
Do not replay interrupted or uncertain inputs.

To roll back, stop Oak through that same active checkout, restore the exact
saved config, switch the clean source checkout to the recorded rollback branch,
and start Oak once. Preserve SQLite state, inbox, workspaces, runtime auth and
all other service units. A source checkout with unexpected local changes must
be reviewed before switching; do not reset or discard it blindly. Disable the
separate broker socket/service only if no longer needed; do not stop or modify
the existing NeMo service merely to roll back this gateway client.
