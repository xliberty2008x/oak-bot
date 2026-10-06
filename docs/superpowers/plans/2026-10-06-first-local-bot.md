# Oak implementation map

Oak is organized around a reusable controller and independently configurable
transports and tools. Private personas, memories and credentials are deployment
data. The current architecture is described in [design.md](../../design.md).

| Area | Source | Validation |
| --- | --- | --- |
| Runtime and event projection | `oak/runtime.py`, `oak/events.py` | Subscription auth, streamed text, lifecycle and interactive request checks |
| Conversation control | `oak/controller.py` | Start/resume, steering, stop, durable inputs and restart handling |
| Telegram | `oak/telegram.py` | Allowlist, attachments, approvals, streaming and durable outgoing state |
| Memory and context | `oak/memory.py`, `oak/context.py` | Explicit selected imports and conversation-scoped retrieval |
| Scheduling | `oak/schedule.py` | One-time/recurring jobs, idle-chat dispatch and uncertain recovery |
| Tool dispatch | `oak/tools.py`, `oak/interaction.py` | Native tool routing, owner responses and artifact handoff |
| Media, research and browser | `oak/media.py`, `oak/research.py`, `oak/browser.py` | Real synthetic artifacts and public research checks |
| Setup and operation | `oak/__main__.py`, `scripts/bootstrap.sh` | Doctor, smoke, configured run and service lifecycle |

Use focused standard-library tests and concrete local checks. Record live
Telegram and provider verification separately in [validation.md](../../validation.md).
Do not add private deployment content or generated outputs to public source.
