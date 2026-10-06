# Oak

Oak is a personal assistant harness for Telegram and web. It keeps conversations and
selected memories, accepts corrections while working, runs tools, creates media,
and handles reminders and scheduled tasks. Each deployment supplies its own
private persona and configuration.

Oak uses ChatGPT subscription authentication and the exact model
`gpt-6.1-sol`. There is no automatic API-key or model fallback.

## Capabilities

| Area | What Oak provides |
| --- | --- |
| Conversation | Streaming replies, ongoing-task steering, cancellation and persistent sessions |
| Telegram | Private-chat allowlist, rich messages, safe classic fallback, typing, photos/files/voice input and durable delivery |
| Web / Mini App | Authenticated conversations, streamed replies, approvals, artifact downloads and accessible tables |
| Events | Durable AG-UI event stream with independent conversation/run subscribers and separate tool results |
| Memory | Per-conversation notes, full-text retrieval, forgetting and selected-file import |
| Scheduling | One-time reminders, recurring reminders and scheduled assistant tasks |
| Tools | Workspace files and runtime tools, interactive approvals and follow-up questions |
| Images | Native image generation when available to the signed-in account; local Unicode title cards and thumbnails |
| Video | FFmpeg MP4 previews and image montages |
| Voice | Local Piper narration and local Vosk or optional faster-whisper transcription |
| Research | Public web search, readable pages and public YouTube metadata |
| Browser | A dedicated Playwright profile with navigation, reading, clicking, typing and screenshots |
| Computer use | Configured Linux X11 desktop, visual screenshots returned to the model, mouse, keyboard, dragging and scrolling |

Connected applications require their own configured access. Tool availability and
local checks are recorded separately from live Telegram results in
[validation](docs/validation.md).

## Setup

Use Linux, Git, Node/npm, Python 3.11+ with venv/pip, FFmpeg and DejaVu Sans fonts.
Autostart requires cron with `crontab`; temporary HTTPS preview uses `cloudflared`
or OpenSSH, depending on the selected provider.
The agent-runtime dependency
is the official `codex` CLI; version `0.159.2` is the initial validated version.
The signed-in ChatGPT account must have access to `gpt-6.1-sol`.

```bash
git clone https://github.com/xliberty2008x/oak-bot.git
cd oak-bot
npm install -g @openai/codex@0.159.2
mkdir -p .state/runtime
chmod 700 .state .state/runtime
env CODEX_HOME="$PWD/.state/runtime" codex login --device-auth
./scripts/bootstrap.sh --browser --voice
```

Bootstrap creates `.venv`, installs the pinned Python dependencies, creates a
local config if missing and checks account/model availability. `--browser`
installs Chromium. `--voice` downloads the public Ukrainian narration and
recognition models outside the repository and prints configuration paths to
copy into your local config. Omit either option when it is not needed. Install
system packages separately.

For desktop control, install `xdotool` and `xmodmap`, provide a running X11 session and use
`--computer` to check the required local dependencies. Enable the explicit
display in your private config; see [computer use](docs/configuration.md#computer-use).

Store the Telegram token with hidden terminal input:

```bash
.venv/bin/python scripts/set_telegram_token.py
```

Edit `config.local.json`: set `telegram_username` and explicit numeric
`allowed_user_ids`. The token file, persona, memories and runtime state stay
outside version control. See [configuration](docs/configuration.md) for voice
models, memory import and optional integrations.

The optional web interface shares the Telegram conversation and can create
independent conversations. Enable it in your private config and provide an HTTPS
endpoint to use it as a Telegram Mini App. See the
[web configuration](docs/configuration.md#web-and-telegram-mini-app) for standalone
login and temporary tunnel setup.

```bash
.venv/bin/python -m oak doctor --config config.local.json
.venv/bin/python -m oak smoke --config config.local.json
.venv/bin/python -m oak run --config config.local.json
```

`smoke` performs real subscription-backed model calls. It checks streaming,
continuation, steering and cancellation without contacting Telegram.

## Use in Telegram

Send a task, photo, file or voice message. Send another message while Oak works
to steer the current task. Voice input requires a configured local recognizer.

| Command | Action |
| --- | --- |
| `/help`, `/status` | Show commands or current state |
| `/web` | Open the configured HTTPS web / Mini App interface |
| `/stop`, `/new` | Stop the current task or start a fresh conversation when idle |
| `/remember text`, `/memory query` | Save or search a private note |
| `/remind 60 text`, `/tasks`, `/cancel ID` | Create a reminder, inspect or cancel a scheduled task |
| `/image title`, `/preview title` | Render a title card or short MP4 |
| `/speak text` | Produce local Ukrainian narration |
| `/research query`, `/browse URL` | Search the web or open a browser page |
| `/approve ID`, `/deny ID`, `/answer ID text` | Respond to an approval or a question |

Use natural language for richer workflows, including recurring schedules,
selected memory updates and creating files. Browser actions that publish,
purchase or delete should be explicitly confirmed with the owner.

## Keep Oak running

```bash
.venv/bin/python -m oak service start --config config.local.json
.venv/bin/python -m oak service status --config config.local.json
.venv/bin/python -m oak service stop --config config.local.json
.venv/bin/python -m oak service install-autostart --config config.local.json
```

Run one owner process per bot. Startup verifies the token's bot identity and
refuses an existing webhook. Persist the configuration, workspace and state
across restarts. Accepted inputs and outgoing delivery state are durable;
ambiguous sends or interrupted tasks are marked uncertain instead of being
silently repeated. Exactly-once external delivery is not promised.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Read the [architecture](docs/design.md), [configuration](docs/configuration.md)
and [validation record](docs/validation.md). Keep account credentials, browser
profiles, private instructions, user data and generated artifacts out of commits.
