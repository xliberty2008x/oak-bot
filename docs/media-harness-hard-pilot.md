# Harder media procedure pilot

This second opt-in pilot tests whether a reusable procedure improves Oak's
results on harder, source-preserving media tasks. It builds on the first
[media procedure pilot](media-harness-pilot.md) and its implementation in
[PR #103](https://github.com/xliberty2008x/oak-bot/pull/103). The first experiment
found equal held-out success and slower candidate execution. Its tasks, results
and procedures are not reused as held-out evidence in this experiment.

The harder suite has 12 new synthetic tasks: six development tasks and six
held-out tasks, with two repetitions fixed before development. Each split has
three creation and three repair tasks. The tasks cover local eyelid animation,
pivoted pendulum motion, foreground occlusion, continuous elliptical motion,
composition of fine text layers, and restoration of textured archival cards.
The held-out versions use new artwork, text, dimensions and motion parameters.
They test transfer within these six task families, rather than six unrelated
media capabilities.

The supplied broken repair seeds contain deliberate defects. They establish
that the task requires repair; they do not establish that the current Oak
procedure fails. The baseline stage measures current behavior on development
tasks before candidate procedures are authored. A ceiling result must be
reported as such, not converted into a claimed improvement.

## What the quality measurement means

Each task supplies original synthetic raster layers and a `scene.json`
production specification. Placements, timing formulas, protected regions and
acceptance tolerances are disclosed to every arm. The producer can read its
own task inputs; it cannot read the reference compositor, scorer, other tasks
or held-out results. Tasks require preserving supplied artwork and text layers,
not approximating them with redrawn shapes or retyped text.

The existing Oak controller and sampled independent artifact reviewer remain
fixed across arms. Adapter v2 supplies the reviewer with the original hashed
JSON production specification and trusted FFprobe pixel-format facts. These
are the same requirements and factual media metadata available to each
producer, without independent scorer results or producer-authored assertions. A separate deterministic scorer decodes the resulting
artifact at full resolution and, for video, checks every frame. It measures
source preservation, critical small regions, layering, continuous motion and
the disclosed composition rules, alongside format and timing requirements.
Compression and small placement tolerances avoid requiring identical encoded
bytes. For video, source compositions and the stationary baseline pass through
FFmpeg's uncompressed RGB → yuv420p → RGB conversion before comparison. A fixed
2/255 channel allowance accounts for small H.264 quantization errors; PNG uses
native RGB and no allowance. These rules and regional thresholds are disclosed
in the input contract and frozen before model measurement. SHA-256 binds a
score to the exact immutable output that was measured.

Authored correct compositions for all 12 tasks and deliberate negative controls
validate the scorer before the experiment. This construction check contains no
Oak model results. Candidate authors receive only development evidence, and
held-out model runs begin only after procedure selection is frozen.

The source-contract endpoint scores the final candidate after a completed
producer turn in every arm. Finished attempts with missing candidates or
incomplete execution count as zero, so a reviewer rejection cannot selectively remove difficult outputs
from the denominator. `quality_passed` and `quality_score` describe this
endpoint, rather than general aesthetic quality; invalid formats also receive zero score. Failed or timed-out attempts remain failures even if partial bytes
were retained for diagnosis.

The end-to-end `passed` endpoint separately requires an actually delivered
artifact whose exact SHA-256 has both a reviewer pass and an independent
content pass, as well as completed execution and unchanged source inputs. A
reviewer pass cannot override a disclosed defect; a passing undelivered
candidate is not a successful user result. If final candidate bytes differ
from the delivered bytes, both outputs receive their own measurement.

This measures adherence to explicit synthetic media requirements. It does not
measure general aesthetics, naturalistic animation, arbitrary OCR, external
generation providers, audio, live Telegram delivery or quality on real user
artwork. A higher score supports a bounded claim about this suite only.

## Setup and run

Use the setup and sandbox dependencies described in the
[first-pilot guide](media-harness-pilot.md#setup): Python 3.12, the optional pilot
requirements, Pillow, NumPy, Bubblewrap, FFmpeg/FFprobe with H.264 support, and
DejaVu Sans fonts. Use a complete Codex distribution, including its matching
code-mode host, on `PATH` and an existing ChatGPT-authenticated runtime home.
The model remains `gpt-6.1-sol`; there is no API-key fallback.

Choose an empty private experiment directory outside the checkout. The
following commands use portable example paths; supply the existing authorized
runtime home directly without copying authentication state.

```sh
.venv-media-pilot/bin/python -m oak.media_pilot init --suite hard --output /private/oak-hard-media-run --repeats 2 --timeout 600 --effort high --workers 1
.venv-media-pilot/bin/python -m oak.media_pilot baseline --suite hard --output /private/oak-hard-media-run --runtime-home /private/existing-codex-home
.venv-media-pilot/bin/python -m oak.media_pilot develop --suite hard --output /private/oak-hard-media-run --runtime-home /private/existing-codex-home
.venv-media-pilot/bin/python -m oak.media_pilot freeze --suite hard --output /private/oak-hard-media-run
.venv-media-pilot/bin/python -m oak.media_pilot evaluate --suite hard --output /private/oak-hard-media-run --runtime-home /private/existing-codex-home
.venv-media-pilot/bin/python -m oak.media_pilot report --suite hard --output /private/oak-hard-media-run
```

`baseline` runs only the six development tasks, twice, under the current
procedure. Inspect its independently measured defects before proceeding. The
hard-pilot development gate requires observed content failures; unavailable
runtime access or a ceiling result does not satisfy this condition.
`develop` authors two bounded reusable procedures from development evidence
and runs each on the same six tasks, twice. The proposer receives development
briefs and findings, without tools or access to the experiment directory.

`freeze` records the selected procedure, challenger, selection evidence and
hashes. It prioritizes joint delivered quality success, then source-contract success
and mean source fidelity. Candidates cannot worsen delivery, source compliance,
structural validity, reviewer uncertainty or reviewed material-defect counts. The baseline stays eligible and
wins exact ties. `evaluate` compares
the baseline with the frozen challenger on the six new held-out tasks, twice.
It does so even if the development selection retained the baseline, allowing
the challenger to be tested without promoting it. A complete experiment has
60 producer attempts: 12 baseline development, 24 candidate development and
24 held-out attempts, plus proposal and artifact-review calls.

Model, reasoning effort, reviewer, permissions, review budget, task inputs and
per-task execution budget are identical across arms. Arm order rotates across
tasks and repetitions. Each producer starts with empty conversation memory.
The renderer exposes only its run workspace and public dependencies; it has
no network or access to the scorer, checkout, runtime home or another run.

The private state records completed attempts and can resume them. Interrupted
attempts remain in its ledger, and a retry uses a fresh workspace. Experiment
implementation, task suite, selected settings and frozen procedures are bound
by hashes. Changes require a new experiment directory. Do not edit receipts,
artifacts or task definitions to repair a score. Held-out feedback must not
be used to revise a procedure in the same experiment.

## Read and report the evidence

Use `report.json` and `report.md` alongside the individual receipts. Compare
independently passing delivered outputs, execution and delivery completion,
source preservation, reviewer disagreements, first-review outcomes, review and
tool calls, and elapsed time. Inspect the named failed content checks, rather
than treating a continuous score as a universal quality scale.

Keep joint delivered quality success and completed-candidate source compliance separate. In
particular, distinguish a reviewer-approved delivered artifact that fails a
content check from a completed passing candidate blocked before delivery.
The latter is evidence about a pipeline bottleneck; attribute it to the
reviewer only when the rejection and candidate concern the same SHA-256.
Earlier defects that were repaired are not defects in the final delivered
file. A failed pair on both arms is a tie, never a success.

Report paired outcomes by held-out task and repetition, with all missing and
failed attempts in the denominator. Two repetitions help reveal instability
but do not turn six task families into 12 independent task families. Use
task-level agreement and regressions when judging whether an observed gain
is credible; do not claim statistical certainty from this small suite.
The mean score in an in-progress report covers finished attempts only. Read
it alongside finished/expected counts and make improvement claims only after
the planned comparison is complete.

A preliminary development-only construction audit exposed an incoherent blink
formula: its exact output passed the source-contract check while the visual
reviewer correctly reported detached intermediate contours. That run and one
interrupted attempt are preserved in the excluded construction ledger. No
candidate or held-out model run had occurred. The corrected curved-aperture
reference was inspected on every intermediate frame before a new experiment.
This is why source compliance is reported separately from joint visual success.

The actual comparison results are pending. This guide describes the protocol
and commands; it does not assert that the current procedure fails the new
development set or that any challenger improves held-out quality. None of
these commands promotes a procedure into production experience or changes
Oak's live behavior.
