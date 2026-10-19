# Complex media procedure comparison

The `complex` suite tests source-preserving media work that requires projective
registration, multiple depth masks and coupled rigid motion. It follows the
[harder-media protocol](media-harness-hard-pilot.md). All 60 planned native
producer attempts finished. The selected learned procedure does not demonstrate
a quality improvement on the fresh held-out tasks. Retain the current media
procedure; do not promote either learned candidate from this study.

## Completed held-out comparison — 19 October 2026

The [full results](evaluations/hard-media-harness-pilot-2026-10-19/complex-results.json)
retain all 60 nominal attempts, both learned procedures, exact evidence hashes,
all 12 paired held-out task/repetition outcomes and three interrupted unknown
attempts. Joint delivered and final-candidate source-property passes are separate
endpoints; their counts happen to agree in this experiment.

| Held-out arm | Joint delivered | Candidate source properties | Mean source score | Tool calls | Median native seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Current procedure | 11/12 | 11/12 | 0.952198 | 205 | 86.760 |
| Frozen candidate 1 | 7/12 | 7/12 | 0.885154 | 205 | 89.250 |

Formal paired outcomes are **1 candidate win, 5 losses and 6 ties**. All 24
held-out outputs passed format checks and their first visual review, with no
unverified review. These aggregate source scores and disagreements require the
sampling caveat below; they are not counts of established visible defects.

| Held-out family | Current procedure | Candidate 1 | Paired candidate wins / losses / ties |
| --- | ---: | ---: | ---: |
| Projective print creation and repair | 4/4 | 4/4 | 0 / 0 / 4 |
| Depth creation and repair | 3/4 | 1/4 | 1 / 3 / 0 |
| Rigid mechanism creation and repair | 4/4 | 2/4 | 0 / 2 / 2 |

Both creation repeats in the new depth scene pass for baseline and fail for the
candidate. Repair has one win in each direction. Thus the depth result does not
support improvement independently of the disputed mechanism criterion.


A separate postcompletion [held-out source audit](evaluations/hard-media-harness-pilot-2026-10-19/holdout309-depth-corroboration-audit.json)
corroborates the first creation pair. At frame 12, candidate 1 replaces 767 exposed
source pixels with stationary mural, including 118 cream-ring pixels, 3–10 px
from true foreground; the declared edge allowance is 2 px. Baseline preserves
those original pixels before encoding. The
[original / baseline / candidate crop](evaluations/hard-media-harness-pilot-2026-10-19/holdout309-baseline-candidate1-source-proof.png)
shows the corresponding loss in the immutable videos. All 36 frames were inspected.
This is an actual local source-fidelity defect, separate from the marker sampling
ambiguity. It adds no metric, reclassification or learner feedback, and the
candidate's separate repair win remains in the paired results.

Both formal mechanism losses concern only the tiny joint-marker pixel comparison.
The DEV audit below demonstrates that this check can reject a valid pixel-center
construction inside the declared geometric allowances. The held-out failures
remain recorded and unresolved as media defects; they are not automatically
reclassified as valid. Even treating the whole mechanism family as inconclusive
leaves a depth regression and a print ceiling, not evidence for promotion. The
next scorer version needs coherent index/pixel-center positive controls and
fresh evaluation; this trial's thresholds, selection and outcomes stay intact.

There were three external CLI exits with code 143 and unconfirmed causes. Each
unfinished attempt resumed in a fresh workspace; finished successes and failures
were retained. The ledger therefore contains 60 completed nominal attempts and
three preserved unknown originals, or 63 initiated attempts. The separate
conservative held-out diagnostic is 11/13 for baseline and 7/12 for candidate 1;
it gives unknown attempts no success credit without calling them content failures.
It does not replace the frozen primary denominator or selection rule. This
sensitivity still supplies no improvement evidence. Later individual PTY jobs
finished normally; their success does not establish the earlier signal's cause.

Elapsed time and tool counts are cost proxies on a shared host, not a production
latency or token/USD benchmark. No live media delivery or production lesson
promotion occurred.


The [independent evidence QA](evaluations/hard-media-harness-pilot-2026-10-19/complex-independent-qa.json)
passed 51/51 arithmetic/provenance checks, including all 385 distinct source and
snapshot paths. The exporter separately rechecks 325 source entries and 120
snapshot entries, preserving duplicate receipt bindings. Counts are checks, not
445 distinct files. The [resource cleanup receipt](evaluations/hard-media-harness-pilot-2026-10-19/execution-resources.json)
records stopped native processes, one removed temporary swap file and one task-owned
1 GiB swap file left active to preserve memory headroom. No persistent boot
configuration was changed.

## Completed development and frozen choice

All 36 planned development producer attempts finished at the same fixed
`gpt-6.1-sol` low effort. Each arm has six tasks with two repetitions.

| Arm | Joint delivered passes | Candidate source-property passes | Mean source-property score |
| --- | ---: | ---: | ---: |
| Current procedure | 9/12 | 9/12 | 0.925464 |
| Learned candidate 1 | 9/12 | 9/12 | 0.931805 |
| Learned candidate 2 | 9/12 | 9/12 | 0.923359 |

All three arms passed format checks and their first visual reviews in all 12
attempts, with no unverified review. The fixed selection rule chose candidate 1
on its secondary mean source score after equal primary success and unchanged
nonregression guards. There is no development task-completion gain. Its procedure
hash is `ab96dca079badc14cdb5e5f989dd44b2e99f582e52d97c38a90946fd12b454f7`;
freeze bound all development evidence at 15:37:04 UTC on 19 October 2026.


The tool-free proposer received baseline DEV briefs, named checks, bounded
metrics, render-call receipts and visual-review text. Render receipts identify
code by hash; original images, produced frames, render code and the manual audits
were not proposal inputs. This tests reusable procedure changes guided by that
limited evidence. It does not evaluate every tool or feedback component of a
full recursive harness.

Independent DEV-only audits explain why numeric checks and visual review both
remain necessary:

- The [first baseline depth audit](evaluations/hard-media-harness-pilot-2026-10-19/native-first-defect-audit.json)
  confirms that a mask replaced 602 original subject pixels with background at a
  frame-13 gap, outside the disclosed boundary allowance and before encoding.
  The visual reviewer had passed that artifact.
- [Candidate 2's first successful depth example](evaluations/hard-media-harness-pilot-2026-10-19/candidate2-dev303-corroboration-audit.json)
  restored that gap, as shown in the
  [baseline / candidate / source crop](evaluations/hard-media-harness-pilot-2026-10-19/dev303-baseline-candidate2-source-proof.png).
  Its second repeat of the same task failed (score 0.800566). This illustrates
  one DEV success, not consistent or held-out improvement.
- The [candidate-1 mechanism sampling audit](evaluations/hard-media-harness-pilot-2026-10-19/candidate1-dev305-sampling-audit.json)
  found a scorer-sensitive rejection. All 36 kinematic positions match exactly;
  independently reconstructed original-part frames match the producer's
  pixel-center convention exactly, and decoded marker centers stay within
  0.75 px of their targets, below the declared 3 px allowance. The canonical
  and pixel-center raster conventions differ by less than 1 px. No actual
  disconnection, stretching or repainting was established. The original failure
  and score remain unchanged; this is a limitation of interpreting the frozen
  source-property endpoint, rather than evidence of a visible regression.

These audits did not enter the proposer input or change procedures, thresholds,
selection or metrics. The execution source is commit
`4fa96e99e5e3a8934f11357574976faa604638c4`; later documentation commits are not
substituted for that source identity.

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
