# Complex media procedure comparison

The `complex` suite tests source-preserving media work that requires projective
registration, multiple depth masks and coupled rigid motion. It follows the
[harder-media protocol](media-harness-hard-pilot.md). Its settings and procedures
must be sealed in a fresh experiment after authored validation; results are
pending, and this guide makes no improvement claim.

The preceding
[perceptual development calibration](evaluations/hard-media-harness-pilot-2026-10-19/perceptual-development-calibration.json)
finished 12/12 explicitly high-effort baseline attempts with both source-property and joint delivered
passes, all on the first visual review. Its gate therefore refused candidate
authoring. No held-out model feedback was used. Those results establish a ceiling
for that development set; they are preserved separately from the new suite.

## Tasks and validity

There are 12 fresh task IDs: six development and six held-out tasks. Each split
contains one creation and one repair task in each family:

- Register a higher-resolution donor to a perspective-distorted print using
  four intact, uniquely labelled landmarks, then restore only a guided stain.
  The target is sampled directly from the supplied donor, so the requested
  detail is recoverable rather than information lost before the donor existed.
- Move two patterned subjects at different depths through two opaque foreground
  objects. Shared foreground/background pigments prevent a global color mask
  from solving the task; the public guide and visible contours identify depth.
- Assemble two complete rigid source parts around their visible pivot markers
  and track a public endpoint loop on the declared inverse-kinematic branch.
  A clean base and complete part textures avoid inventing newly exposed content.

Before any main model output, authored controls must establish recoverable donor
sampling neighborhoods, nonempty critical print supports, actual depth crossings,
reachable endpoints and complete visible parts throughout all frames. Valid
alternate registration/interpolation and subpixel phases need positive controls;
matching one canonical reconstruction is insufficient to validate a tolerance.
Negative controls must isolate meaningful defects such as wrong depth, detached
joints, changed source patterns, misregistration or damaged text. Any adjustment
to public acceptance rules happens before the fresh seal, using authored controls
rather than model-output feedback.

The existing adapter v3 supplies at most three role-labelled, explicitly hashed
original references. Its public `review_manifest` separates visible requirements
from mechanical measurements. The producer receives the full original task and
specification; the unchanged visual reviewer receives the original visual scope,
references and trusted image/codec facts. Numeric grader results, expected outputs
and private annotations are not supplied to either model during a task.

The primary `passed` endpoint still requires completed execution, unchanged
inputs, and visual plus independent property passes for the same delivered SHA.
Final-candidate property scores are a separate diagnostic. A passing undelivered
file or a numerical match with a visible material defect is not joint success.
These checks do not provide human aesthetic ground truth.

## Run short resumable batches

Use the dependencies and authorized ChatGPT runtime described in the
[setup guide](media-harness-pilot.md#setup). Fix the same `gpt-6.1-sol`, low effort,
600-second producer budget, two repetitions and one worker before development.
The low setting matches the public defaults in `Controller.effort_for` and
`RuntimeClient`; both producer and reviewer use it in every arm. The earlier
high-effort calibration is kept separate and does not establish behavior at
the default setting. Choose an empty private directory outside the checkout.

```sh
.venv-media-pilot/bin/python -m oak.media_pilot init --suite complex --output /private/oak-complex-media-run --repeats 2 --timeout 600 --effort low --workers 1
.venv-media-pilot/bin/python -m oak.media_pilot baseline --suite complex --output /private/oak-complex-media-run --runtime-home /private/existing-codex-home --batch-size 1
.venv-media-pilot/bin/python -m oak.media_pilot report --output /private/oak-complex-media-run
```

Repeat the same `baseline` command until all 12 planned development attempts
finish. `--batch-size` accepts 1–36 and limits execution for the current invocation;
it does not reduce the experiment's task or repetition counts. The driver takes
the first unfinished jobs in the existing rotated arm order before scheduling.
Unscheduled jobs create no running records or workspaces. Finished attempts,
including failures, are retained and never replayed merely to obtain a pass.
Omitting the option preserves the full-split execution behavior.

Inspect completed baseline failures before authoring a procedure. The complex
gate requires an immutable, structurally valid final candidate with either a
failed content property or a same-SHA major/blocker visual rejection. Missing
context, minor issues and timeouts do not satisfy this gate. A ceiling result
ends candidate development for that experiment.

```sh
.venv-media-pilot/bin/python -m oak.media_pilot develop --suite complex --output /private/oak-complex-media-run --runtime-home /private/existing-codex-home --batch-size 1
.venv-media-pilot/bin/python -m oak.media_pilot freeze --output /private/oak-complex-media-run
.venv-media-pilot/bin/python -m oak.media_pilot evaluate --suite complex --output /private/oak-complex-media-run --runtime-home /private/existing-codex-home --batch-size 1
.venv-media-pilot/bin/python -m oak.media_pilot report --output /private/oak-complex-media-run
```

Limited `develop` invocations keep phases separate. A call that runs baseline
attempts returns before proposal. Once baseline development is complete, the
next call authors and saves two procedures without running candidate tasks.
Later calls run only unfinished candidate attempts; the proposer is not called
again. Repeat `develop` until both candidates finish their 12 development attempts.
`freeze` refuses incomplete development and keeps baseline eligible under the
existing joint-success and regression rules.

Repeat `evaluate` until the baseline and frozen challenger each finish all 12
held-out attempts. The complete planned comparison still contains 60 producer
attempts: 12 baseline development, 24 candidate development and 24 held-out.
Proposal and review calls are additional. Neither batch boundaries nor CLI
process restarts authorize procedure changes or reduce expected counts. Genuine
interruptions remain in the ledger and use a fresh workspace on resumption.

Implementation fingerprints include the complex task/scorer sources and their
hard/perceptual grader dependencies. A changed implementation requires a new
experiment. Candidate authoring closes at freeze; held-out native outcomes cannot
be used to revise procedures, inputs or acceptance thresholds in the same trial.
Authored technical controls and a development-only visual preflight are recorded
separately from the main comparison.

Report delivered joint passes, candidate-property passes, paired task/repetition
outcomes, verification interruptions and cost together. Keep failed and missing
attempts visible against their planned counts. Three related task families and
two repetitions provide limited transfer evidence; they do not establish general
media-quality improvement, production performance or live delivery behavior.
