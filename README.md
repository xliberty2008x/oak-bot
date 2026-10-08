# Oak

Oak is a personal assistant harness for Telegram and web. It keeps conversations and
selected memories, accepts corrections while working, runs tools, creates media,
and handles reminders and scheduled tasks. Each deployment supplies its own
private persona and configuration.

Oak uses ChatGPT subscription authentication with `gpt-6.1-sol` by default.
The owner can explicitly select a runtime-listed model for each session in the
Mini App. There is no automatic API-key or model fallback.

## Capabilities

| Area | What Oak provides |
| --- | --- |
| Conversation | Streaming replies, ongoing-task steering, cancellation and persistent sessions |
| Telegram | Private-chat allowlist, rich messages, safe classic fallback, typing, photos/files/voice input and durable delivery |
| Web / Mini App | Owner-authenticated control panel: Telegram sessions, task controls, per-session model choice and runtime integration status |
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
| Remote desktop | Owner-operated desktop in the Mini App, with a phone touchpad, mobile keyboard and desktop mouse/keyboard |

Connected applications require their own configured access. Tool availability and
local checks are recorded separately from live Telegram results in
[validation](docs/validation.md).

For manual computer control, open **Робочий стіл** in the Mini App and
connect. Oak pauses agent work while you operate the VM's browser. Use
**Завершити** when finished. See [remote desktop](docs/remote-desktop.md)
for phone controls, authentication and deployment requirements. The shared VM
desktop is accessible to the agent and is not an isolated credential channel.

## Setup

Start with a fresh Ubuntu 24.04 or Debian 12/13 amd64 VM, a normal user with
`sudo`, and Git. The repository includes an agent-readable
[bootstrap skill](.agents/skills/oak-bootstrap/SKILL.md) and an executable installer
for the machine, desktop, browser, voice and gateway.

```bash
git clone https://github.com/xliberty2008x/oak-bot.git
cd oak-bot
./scripts/bootstrap-vm.sh \
  --owner-id YOUR_NUMERIC_USER_ID \
  --bot-username YOUR_BOT \
  --telegram-api-env-file /absolute/path/to/private-telegram-api.env
```

The installer requests the Telegram token through hidden terminal input, or
accepts `--token-file /absolute/private/file`. It uses an isolated ChatGPT login
for Oak; the account must have access to `gpt-6.1-sol`. It prepares an X11 desktop
with a visible browser, enables computer use, starts one supervised bot and
registers its HTTPS Mini App. It also installs the local Telegram Bot API by
default, allowing large attachments in all conversations. Supply your Telegram
application's `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in the private environment
file, or use the hidden interactive prompts. State and credentials live outside
Git. By default, HTTPS uses a managed temporary tunnel whose address can change after restart;
use your own HTTPS endpoint for a lasting address.

To delegate the installation, open this repository with your agent and say:

> Bootstrap this VM and deploy Oak using the repository's oak-bootstrap skill.
> Configure the desktop, computer use, voice and Telegram/Mini App gateway;
> verify the running deployment and tell me which sign-in steps need my input.

See the [bootstrap guide](docs/bootstrap.md) for preparation without credentials,
reruns, readiness checks and required user inputs. The existing
`scripts/bootstrap.sh` remains a dependency helper for machines you already
manage; it does not provision the full VM. See [configuration](docs/configuration.md)
for private persona, memory import and integrations.

Contextual forms and the synthetic broker are reproducible from a reviewed
feature revision with `./scripts/bootstrap.sh --features-only --verify-broker`.
This verifies ordinary submit/cancel and dummy login/OTP/cancel without account
access or a live bot. Full VM bootstrap runs protocol checks before account
access; add `--verify-broker` for Chromium. Instagram remains disabled until a
trusted credential channel is separately verified. See [bootstrap feature checks](docs/bootstrap.md#reproduce-the-feature-checks-without-an-account).

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
.venv/bin/python -m oak service start --config ~/.local/share/oak-bot/default/config.json
.venv/bin/python -m oak service status --config ~/.local/share/oak-bot/default/config.json
.venv/bin/python -m oak service stop --config ~/.local/share/oak-bot/default/config.json
.venv/bin/python -m oak service install-autostart --config ~/.local/share/oak-bot/default/config.json
```

The full VM bootstrap installs autostart; these commands also support manual
operation. Substitute the path supplied to `--config` for custom deployments.
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
