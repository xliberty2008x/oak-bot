# Oak harder-media pilot evidence — 19 October 2026

The main comparison completed 60 planned native producer attempts on 12 fresh
synthetic creation/repair tasks. Held-out formal joint success was **11/12 for
baseline and 7/12 for frozen candidate 1**, with **1 win, 5 losses and 6 ties**.
Two mechanism losses use a sampling-sensitive marker check and are unresolved
as actual media defects. The separate depth comparison is **3/4 versus 1/4**;
a postcompletion pixel/video audit confirms real source loss in the first
candidate creation failure. **Retain the current media procedure.**

The upload companion completed 28 native attempts and 28 communication reviews.
Both arms completed 6/6 eligible held-out drafts. The additional procedure lost
one held-out continuation-ownership check; it should not become the default.
The targeted structured upload-error repair is independently reviewable in
[PR 105](https://github.com/xliberty2008x/oak-bot/pull/105).

## Main results and corroboration

| Evidence | Scope |
| --- | --- |
| [Complex results](complex-results.json) | All 60 nominal rows, full procedure text, immutable source/artifact bindings, frozen selection, twelve paired outcomes and three interrupted unknown originals. Joint delivery and candidate source properties remain separate. |
| [Independent QA](complex-independent-qa.json) | 51/51 arithmetic, provenance, split and hash checks; 385 distinct source/snapshot paths. No model calls or media rescoring. |
| [Held-out depth audit](holdout309-depth-corroboration-audit.json) and [source / baseline / candidate proof](holdout309-baseline-candidate1-source-proof.png) | First creation pair selected before inspection; actual local mask overfill in candidate 1, including 767 exposed source pixels at frame 12. All 36 frames inspected after the experiment finished. All other results, including the candidate repair win, remain. |
| [First DEV defect audit](native-first-defect-audit.json) and [proof](dev303-frame13-occlusion-proof.png) | Baseline mask overfill independently confirmed before encoding, beyond the disclosed 2 px boundary allowance. DEV-only corroboration, not proposer input. |
| [Candidate-2 DEV corroboration](candidate2-dev303-corroboration-audit.json) and [proof](dev303-baseline-candidate2-source-proof.png) | The first repeat restored the known gap; the second failed source checks. An illustrative single success, not transfer evidence. |
| [DEV mechanism sampling audit](candidate1-dev305-sampling-audit.json) and [proof](candidate1-dev305-sampling-proof.png) | Exact source geometry and pixel-center construction inside declared tolerances were formally rejected by a tiny-marker comparison. Preserve its failure and the same caveat on two held-out marker-only failures. No automatic reclassification. |
| [Resource cleanup](execution-resources.json) | Native experiment processes stopped. One temporary swap file removed; one task-owned 1 GiB file remains active to preserve memory headroom. No boot configuration changes. |

The execution source is commit
`4fa96e99e5e3a8934f11357574976faa604638c4`, with fixed `gpt-6.1-sol` low effort,
600 seconds, two repeats and one worker. Procedure 1 was selected after all DEV
arms scored 9/12, by its secondary mean source score. The tool-free proposer saw
DEV briefs, numeric/named checks, code-hash receipts and review text; images,
render code and manual audits were excluded from its input. Procedures and
criteria were unchanged throughout held-out execution.

Three external exits with code 143 have unconfirmed causes. Only unfinished
attempts resumed in fresh workspaces; all completed failures stayed. Nominal
60 attempts plus three preserved unknowns make 63 initiated attempts. The
separate conservative held-out fractions are 11/13 and 7/12. They do not replace
selection or primary denominators, and unknown interruptions are not content
failures. PTY/quiescent launches later completed normally without establishing
the earlier signal's cause.

## Upload recovery and browser checks

| Evidence | Scope |
| --- | --- |
| [Upload results](upload-recovery-results.json) | 28 real Controller/native producer attempts, real local media/preflight tools, a simulated site and 28 independent final-reply reviews. Includes all paired outcomes, full synthetic receipts, failed continuation checks and immutable accepted-payload archive bindings. |
| [Local Chromium fixtures](browser-local-fixture-checks.json) | Three actual sandboxed Chromium/localhost checks: hidden cross-origin input, dynamic chooser bytes and uncertain dispatch exactly once. No live provider access. |

The upload tests use short synthetic clips with valid sparse ISO-BMFF padding.
They do not replay the private seven-minute incident, benchmark its encoding
cost or establish live YouTube behavior or a multi-turn human handoff. Exact-byte
blocks are reported separately from eligible draft completion. Structured errors
improve available size/limit/stage evidence; the unchanged binary completion
rubric did not show a rate gain.

## Separate construction and development calibrations

These stages are retained and excluded from main comparison denominators.
No model-produced held-out feedback from them selected the final procedures.

| Evidence | Disposition |
| --- | --- |
| [Rejected construction](excluded-construction-audit.json) | Initial explicit blink construction had incoherent intermediate contours; replaced before candidate development. |
| [Explicit calibration](explicit-specification-calibration.json) | Source-property ceiling: 12/12; joint delivery 10/12 because two reviews lacked required context. No candidates or held-out runs. |
| [Perceptual calibration](perceptual-development-calibration.json) | Explicitly high-effort baseline ceiling: 12/12 joint/source; failure gate refused proposals. No candidates or held-out runs. |
| [Earlier authored review controls](authored-visual-preflight.json) | Construction/reviewer controls, separate from producer attempts. |
| [Complex authored review controls](authored-complex-visual-preflight.json) | Three DEV controls passed the unchanged visual review at low effort before the main seal. |

Twelve synthetic tasks and correlated repeats provide limited transfer evidence.
Source/geometry scores and native visual reviews are not human aesthetic ground
truth. The stored `reviewer_false_pass(es)` names denote a visual/content-check
disagreement; sampling-sensitive rejections are not proven reviewer mistakes.
Future scorer repairs need authored alternative-convention positives and semantic
negatives before a fresh experiment. Existing outcomes remain unchanged.

No private incident media, credentials, production memories, live Telegram
sending, provider upload, merge or deployment was used. See the
[complex guide](../../media-harness-complex-pilot.md),
[upload guide](../../media-harness-upload-pilot.md) and
[harder-media protocol](../../media-harness-hard-pilot.md) for commands and limits.
