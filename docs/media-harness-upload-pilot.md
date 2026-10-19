# Offline upload recovery comparison

This companion to the harder media pilot measures completing an upload workflow
and explaining a blocker. Source-preserving rendering and successful destination
delivery are separate outcomes. A media quality pass alone cannot establish
that a website accepted a video or that Oak helped its owner effectively.

The motivating failure was a local upload-size constraint whose reason was lost
as a class-only `ValueError`. The production repair is independently reviewable
in [PR 105](https://github.com/xliberty2008x/oak-bot/pull/105). The pilot never
uses private incident content, accounts, cookies or conversation memories.

## What runs for real

Every attempt starts a fresh actual `Controller` and ChatGPT-authenticated
`RuntimeClient` using `gpt-6.1-sol`, low effort and a 300-second producer budget.
The same tools, task files, model and budget are available in every arm. Native
shell, apps, web and delegation are disabled; local Python/FFmpeg execution uses
the existing bounded offline render sandbox. `BrowserTools` performs its actual
workspace validation, size limit and immutable file snapshot.

Only the video website is simulated. Its observed state distinguishes file
selection, server acceptance, processing and a saved private draft. Some actions
accept a payload and then time out, so another upload can create a detectable
duplicate. No public publication is authorized, and no real provider is contacted.

The supplied short synthetic H.264/AAC clips have valid sparse ISO-BMFF `free`
atoms that make the originals genuinely exceed the adapter's 128 MiB limit.
Preparing a separate rendition runs real local media tools. These few-second
fixtures do not reproduce a seven-minute production encoding workload or prove
live YouTube compatibility. Original bytes must remain unchanged.

## Comparison and split

Four development tasks cover an oversized original, choosing among existing
renditions, uncertainty after dispatch, and a byte-exact archival constraint.
Four fresh held-out variants change media content, filenames, dimensions,
payload size, chooser behavior and the stage that times out. Tasks requiring
exact original bytes must remain honestly blocked under the authorized route;
silently transcoding them is a failure.

Development has one attempt per task in three arms:

- `baseline`: current Controller and the historical class-only local error.
- `structured`: identical Controller with the repaired structured preflight.
- `procedure`: structured preflight plus the predeclared reusable recovery method.

The procedure and independent communication rubric are fixed in source before
the first native attempt. This is a targeted, authored regression comparison,
not a claim that a learner discovered a better method. The structured-only
development arm helps distinguish tool observability from added procedure text.

After all 12 development attempts, freeze closes development. Two attempts per
held-out task compare baseline and the frozen procedure bundle, with arm order
rotated by task and repetition. The full plan has 28 producer attempts and up to
28 independent communication reviews. Held-out results cannot revise procedures,
inputs, rubric or numeric acceptance criteria in that experiment.

## Evidence and limits

The runner independently verifies unchanged originals, full clip/video/audio
content, accepted payload count, processed state, saved private draft and absent
publication. These deterministic facts determine workflow success. A separate
fresh, tool-free native review evaluates the final Ukrainian explanation against
the same fixed rubric: supported diagnosis, honest completion, necessary owner
burden, concrete blocker, retained responsibility and clarity. That model review
is diagnostic evidence, not human ground truth.

Report completed drafts among tasks where completion is possible separately
from correct handling of an unavoidable block. Also report duplicates,
unauthorized changes, missing/failed attempts, independent-review uncertainty,
tool calls and latency. Keep all finished failures. Genuine interrupted attempts
remain in the ledger and resume in fresh workspaces; a stored producer outcome
is retained if only its communication review was interrupted.

```sh
.venv-media-pilot/bin/python -m oak.media_pilot_upload init --output /private/oak-upload-run
.venv-media-pilot/bin/python -m oak.media_pilot_upload run --output /private/oak-upload-run --runtime-home /private/existing-codex-home --split dev --batch-size 1
.venv-media-pilot/bin/python -m oak.media_pilot_upload freeze --output /private/oak-upload-run
.venv-media-pilot/bin/python -m oak.media_pilot_upload run --output /private/oak-upload-run --runtime-home /private/existing-codex-home --split holdout --batch-size 1
.venv-media-pilot/bin/python -m oak.media_pilot_upload report --output /private/oak-upload-run
```

Repeat each `run` command until that phase finishes. Source, task, procedure,
judge and configuration fingerprints reject changes after initialization.
Only a completed offline comparison can support a result; this guide records the
protocol and makes no quality-improvement claim before results exist.
