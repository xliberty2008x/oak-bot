# Media procedure pilot: 2026-10-19

The pilot completed all 30 producer runs on 12 synthetic media tasks. The frozen
challenger did not improve held-out success and had higher median latency.
Retain the current Oak procedure. The reusable result of this work is the
isolated comparison command and its recorded evidence.

## Results

| Split | Procedure | Success | First-review pass | Review sessions | Tool calls | Median seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Development | Current Oak | 6/6 | 5/6 | 7 | 88 | 79.187 |
| Development | Candidate 1: artifact-bound checkpoints | 6/6 | 6/6 | 6 | 85 | 88.706 |
| Development | Candidate 2: source-envelope checks | 6/6 | 5/6 | 7 | 90 | 96.354 |
| Held out | Current Oak | 6/6 | 5/6 | 7 | 88 | 92.394 |
| Held out | Frozen candidate 1 | 6/6 | 6/6 | 6 | 88 | 105.116 |

The six held-out pairs are six ties on final task success: zero candidate wins
and zero losses. Candidate 1 saved one held-out review session, used the same
number of tool calls, and had 13.8% higher median elapsed time. A smaller review
count alone does not establish a quality improvement.

All three additional reviews across development and held-out evaluation changed
from `unverified` to `pass` for unchanged artifact bytes. They concerned
technical verification rather than a reported material visual defect. This
shows reviewer variability and limits causal interpretation of first-review
differences. The exact verdict sequences and artifact hashes are retained in
[results.json](results.json).

## Protocol and evidence

- Evaluated source: `c0be72f8449c133ec757bf45926973cbcb3d077e`.
- Model: subscription-backed `gpt-6.1-sol`, high effort, no provider/model
  fallback. Complete Codex 0.159.2 distribution with its matching code-mode host.
- Environment: Python 3.12.15, Pillow 12.3.0, NumPy 2.5.4, distro FFmpeg/FFprobe.
- Settings fixed before development: one repetition, 420 seconds per producer
  run, three concurrent independent workspaces, at most four reviews per task.
- Each split contains three creation and three repair tasks: four PNG projects
  and two short H.264 MP4 projects. Held-out artwork, wording, geometry and
  motion requirements differ from the development projects.
- Development used six baseline runs and six runs for each proposed procedure.
  Candidate 1 was frozen before any held-out run. The author received only
  development evidence. Held-out evaluation then ran six baseline/challenger
  pairs with rotated arm order.
- Every task used a fresh Controller conversation and empty scoped memory, the
  same offline render/inspect tools, and the unchanged independent reviewer.
  Inputs were read-only inside Bubblewrap; other tasks, the scorer, source
  checkout, runtime home and network were unavailable to rendering code.
- A pass required completed production, independently reviewed local delivery
  of the exact SHA-256 bytes, immutable sources, and structural checks.
  Actual container/codec facts were extracted from the same held bytes.
- After scoring, every delivered snapshot was rehashed and all input file
  hashes were compared across arms. All matched. The public JSON includes
  per-run metrics, structural checks, verdict summaries, source hashes, both
  proposed procedures and their frozen identities, with no host paths or
  deployment data.
- The formal experiment contains 30 producer runs, one proposal session and
  33 independent review sessions. Preliminary calibration runs are excluded.

An earlier development calibration exposed missing codec/container facts in
review evidence. It was stopped before held-out execution; its records were
preserved separately. The pilot adapter was corrected, an unchanged reviewer
then passed the actual video, and every formal arm was rerun from the same
corrected source. Calibration outcomes were not used in candidate proposals.

## Validation and limits

The focused pilot suite passed all 44 tests, including actual local Bubblewrap,
read-only input, Pillow/FFmpeg, codec evidence, all 12 task fixtures, isolation,
selection and resume-integrity checks. The same 44 tests also passed in a fresh
virtual environment installed using only `requirements-media-pilot.txt`.
The final application-suite check discovered 1,447 tests: 1,196 executed,
251 skipped, with no failures or errors. Actual independent model checks also
covered image delivery and the complete video codec evidence.

A supplemental blind visual spot-check of both held-out science posters found
the required exact text, complete source illustration, proportional scaling,
readable activity blocks and the specified image geometry/gutters. This is one
spot-check, not a human assessment of every output.

These are small synthetic tasks, one repetition and a model-based visual judge.
The baseline already solved every task, leaving no observed success-rate gain
to demonstrate. Visual evidence is sampled, and typography, source fidelity and
natural motion do not have an independent human ground truth here. Parallel
host load and model variability affect wall time. No conclusion about general
production quality, real user artwork, experience retrieval, live Telegram
delivery, external media providers or audio quality follows from this pilot.
No live procedure, memory or deployment was changed.

See [usage and reproduction](../../media-harness-pilot.md) for the five CLI
phases. Use a fresh experiment and new held-out cases for a subsequent change;
these results must not be used to tune the frozen procedure and then presented
as a fresh held-out evaluation.
