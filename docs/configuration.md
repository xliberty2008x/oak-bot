# Oak configuration

Copy `config.example.json` to `config.local.json` or use the file bootstrap creates.
Run commands from the repository with `.venv/bin/python`. Relative deployment
paths resolve against the configuration file's directory.

| Key | Purpose |
| --- | --- |
| `telegram_token_file` | Private file created with `scripts/set_telegram_token.py` |
| `telegram_username` | Expected bot username; set it before starting |
| `allowed_user_ids` | Explicit positive numeric Telegram user IDs |
| `instructions_file` | Optional private persona or operating instructions |
| `memory_files` | Explicit private files included as startup context |
| `workspace` | Files, artifacts and isolated browser profile |
| `state_dir` | Persistent SQLite databases and process state |
| `runtime_home` | Private runtime account/session directory, separate from desktop sessions |
| `timezone` | IANA timezone for dates without an explicit offset |
| `sandbox` | `workspace-write` or `read-only` for runtime execution |
| `runtime_config` | Runtime options such as approval policy and web search |
| `piper_model` | Optional local `.onnx` narration model with adjacent `.onnx.json` |
| `vosk_model` | Optional extracted local speech-recognition model directory |
| `whisper_model` | Optional local faster-whisper model, used instead of Vosk |
| `search_url` | Optional public SearXNG `/search` URL with JSON output enabled |
| `computer` | Optional X11 desktop control, with an explicit local display |
| `web` | Optional authenticated web/Mini App settings described below |

## Web and Telegram Mini App

Add this object to the private deployment config to enable the web interface:

```json
{
  "web": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8765,
    "public_url": "https://oak.example.com",
    "transport": "sse"
  }
}
```

Serve the local port through a HTTPS reverse proxy or named tunnel. Preserve SSE
streaming and disable proxy buffering, or set `transport` to `poll` for JSON long
polling. Oak registers the HTTPS Mini App menu button only for configured owners.
Telegram signed launch data authenticates the owner; opening the page directly
requires that owner's private access key. Oak creates keys in
`state_dir/web-access-keys.json` with mode 600. An optional `web.access_key_file`
selects another private JSON file mapping owner IDs to keys. Never put keys in a
public URL or commit them.

Embedded clients use a session token held only in page memory, so blocked
third-party cookies do not prevent authenticated polling or artifact downloads.
The official Telegram SDK is cached in the private state directory and served
from the same origin. Initial authentication has a timeout and shows an error
instead of leaving the interface hidden indefinitely.

For a temporary HTTPS preview, install `cloudflared` and add `"tunnel": "quick"`
inside `web`. Oak starts and supervises the tunnel, discovers its public address
and forces polling. The address changes on restart and the owner menu button is
updated. A stable deployment should use its own HTTPS hostname instead.

If that preview hostname is unreachable from a client's network, set
`"tunnel": "localhost"` to use [localhost.run](https://localhost.run/docs/) through
OpenSSH instead. Oak uses the service's keyless connection, keeps server host
keys in the private state directory, and supervises the SSH process. This is
also a temporary preview address; it requires outbound SSH access.

Web and Telegram can show the same conversation. New conversations created in the
web interface have separate model sessions. File and voice uploads currently use
Telegram; the web composer accepts text and displays registered artifacts.

## Local Ukrainian voice

The example deliberately leaves voice model paths unset. Install the default
Ukrainian models with either bootstrap's `--voice` option or the standalone helper:

```bash
.venv/bin/python scripts/setup-voice.py
```

The helper downloads about 154 MB to `~/.local/share/oak-bot/models`, reuses valid
existing files and prints the `piper_model` and `vosk_model` entries to merge into
your private config. `--directory PATH` selects another local model directory.
It pins and checks the Piper model, validates the voice configuration, and
rejects ZIP traversal, links and oversized archives before extracting Vosk.
The helper does not edit your config or include model files in the repository.

| Model | Source and published license information |
| --- | --- |
| `uk_UA-ukrainian_tts-medium` | [Piper voice repository](https://huggingface.co/rhasspy/piper-voices/tree/main/uk/uk_UA/ukrainian_tts/medium) declares MIT; the [voice model card](https://huggingface.co/rhasspy/piper-voices/blob/main/uk/uk_UA/ukrainian_tts/medium/MODEL_CARD) identifies its dataset as CC0 |
| `vosk-model-small-uk-v3-nano` | The [official Vosk catalog](https://alphacephei.com/vosk/models) lists Apache 2.0; the archive includes its license |

For other languages, select matching files using [Piper's instructions](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md)
and the [Vosk catalog](https://alphacephei.com/vosk/models). Use absolute local model paths.
For optional faster-whisper recognition, install `faster-whisper` in `.venv` and
supply an existing local model; transcription does not download a model implicitly.
Small recognition models can mishear speech, so inspect important transcriptions.

The browser uses `workspace/browser-profile`. It starts with its own account
state; credentials are not copied from an existing browser. Install its binary
with `.venv/bin/python -m playwright install chromium` if bootstrap was run
without `--browser`. System libraries may be required by [Playwright](https://playwright.dev/python/docs/browsers).

## Computer use

Oak controls a configured Linux X11 desktop using `xdotool`, `xmodmap` and Pillow's
XCB screen capture. Install these X11 tools with your system package manager and provide
an existing X11 session (a physical desktop or Xvfb). `./scripts/bootstrap.sh
--computer` checks the dependencies; it does not create or replace a desktop.

```json
{
  "computer": {"enabled": true, "display": ":2"}
}
```

Choose your actual local display explicitly; `:2` is only an example. Restart
Oak after configuration changes. `doctor --config config.local.json` checks
that display without capturing its contents. Desktop control acts on the apps
and accounts already open on that display, independently of the shell sandbox
and the separate Playwright browser profile. No browser credentials are copied.

Ask Oak naturally to perform a desktop task. `oak_computer` returns an actual
image to the model after each action, using the image's pixel coordinates. It
supports screenshots, moving/clicking/dragging, scrolling, text, key chords and
short waits. A conversation holds desktop access while its task is active;
other conversations receive a busy result. `/stop` cancels the active task.
Screenshots are private workspace artifacts and are sent to Telegram only when
the assistant calls `oak_send_file`, for example at the owner's request.

Enabling this tool changes the registered tool catalog. On the next message,
Oak preserves the previous native session ID and starts an updated session with
the last 12 turns (at most 24,000 text characters) plus scoped long-term memory.
The original native history remains stored; the carried context is a bounded
excerpt, not a full native-history transfer.

## Memory

`memory_files` supplies startup background context. Restart Oak after changing
these files. To import editable, searchable notes for one conversation:

```bash
.venv/bin/python -m oak import-memory \
  --config config.local.json \
  --source /absolute/path/to/selected-memory.json \
  --chat-id YOUR_NUMERIC_USER_ID
```

A selected JSON export has this shape:

```json
{
  "profile": "Explicitly selected background notes",
  "logs": {
    "selected-note": "Additional information to remember"
  }
}
```

Plain UTF-8 text is also accepted. Imports are explicit additions; repeating an
import adds another set of notes. Select only the intended conversation's data.
Server-hosted memory must first be exported through an authorized source. A
missing local memory folder does not mean the server has no memory.

Selected historical conversation or voice-call text can be imported as background
notes, preserving useful speaker labels, dates and source information. This
supplies historical context without recreating the source runtime's sessions.

## Optional runtime integrations

The example uses `.state/runtime` for runtime account data and sessions. Sign in
with `env CODEX_HOME="/absolute/path/to/runtime" codex login --device-auth` using
the path configured for this deployment. `doctor --config` and `smoke --config`
use that same directory. Oak never copies account credentials automatically.
Keeping a separate runtime directory avoids another desktop process taking
ownership of a bot conversation. Configure needed integrations in this runtime;
they are not implicitly inherited from another account/session directory.

`runtime_config` passes supported options to the runtime dependency while Oak
keeps authentication, provider and model pinned. The default example enables
live web search and on-request approvals. Configure connected applications or
MCP servers only when needed; their account access is separate from Oak's
Telegram token. Store any credentials in private deployment configuration.

`read-only` restricts the runtime's filesystem writes. Oak's memory, scheduling,
media and browser adapters are separate services and still perform their defined
operations. It is not an operating-system isolation boundary for the whole app.
