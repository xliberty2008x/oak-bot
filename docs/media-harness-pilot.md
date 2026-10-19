# Local media procedure pilot

This opt-in developer command compares Oak's current task guidance with two
model-proposed reusable procedures. It uses the existing ChatGPT subscription
runtime and `gpt-6.1-sol`, an independent artifact reviewer, and local
Python/Pillow/FFmpeg rendering. It starts no gateway, sends no Telegram messages,
imports no production memories, and makes no deployment or live-lesson changes.

The experiment borrows the idea of improving the agent's working procedure from
[Harness2](https://github.com/google-research/harness2). It does not install that
runner or train model weights. Oak already has independent artifact review and
conversation-scoped experience; this pilot adds a controlled comparison of
proposed procedures. A previous artifact's `proven` experience receipt does not
establish that the procedure improves later tasks. See
[quality and experience](quality-and-experience.md).

## Setup

Use Python 3.12 with the optional pilot dependencies, an existing
ChatGPT-authenticated Codex runtime home, and the `codex` executable on `PATH`.
Use a complete Codex distribution with its matching `codex-code-mode-host`
helper; a standalone executable can authenticate but fail to dispatch tools.
The host also needs Bubblewrap, FFmpeg/FFprobe with H.264 encoding, and DejaVu
Sans fonts. No API key or external media subscription is used.

```sh
python3.12 -m venv .venv-media-pilot
.venv-media-pilot/bin/python -m pip install -r requirements-media-pilot.txt
.venv-media-pilot/bin/python -m unittest tests.test_media_pilot_render tests.test_media_pilot_tasks tests.test_media_pilot_runtime tests.test_media_pilot
```

Use the same Python environment for the commands below. The renderer mounts that
interpreter's distribution and its Pillow package as read-only dependencies.
It does not mount the source checkout, runtime home, other runs or scorer.
The task's `inputs` directory is read-only while rendered outputs remain
writable. Missing Bubblewrap or unavailable isolation fails closed.

## Run the experiment

Choose an empty private output directory outside the source checkout. Pass the
existing runtime home directly; do not copy its authentication or use a live
conversation database.

```sh
.venv-media-pilot/bin/python -m oak.media_pilot init --output /private/oak-media-run --repeats 1 --timeout 300 --effort high
.venv-media-pilot/bin/python -m oak.media_pilot develop --output /private/oak-media-run --runtime-home /private/existing-codex-home
.venv-media-pilot/bin/python -m oak.media_pilot freeze --output /private/oak-media-run
.venv-media-pilot/bin/python -m oak.media_pilot evaluate --output /private/oak-media-run --runtime-home /private/existing-codex-home
.venv-media-pilot/bin/python -m oak.media_pilot report --output /private/oak-media-run
```

Settings are chosen at `init`: one repetition and a 300-second per-task budget
are the defaults. `--workers` bounds concurrent independent tasks (default 2,
maximum 3). Repetitions can be 1, 2 or 3; the budget can be 30–900 seconds.
The same model, effort, tools, reviewer policy and budgets apply to every arm.
Each render invocation is separately limited to 60 seconds, 64 KiB of Python
source, bounded output, 1 GiB of address space, 45 CPU seconds and 128 MiB per
file. Rendering has its own network and PID namespaces and a clean environment.
Each task permits at most four independent reviews. Timeouts and unavailable
reviews never become passes.

At the default repetition count, development runs six baseline tasks, proposes
two procedures from their results, then runs each candidate on those same six
tasks: 18 producer runs. The proposer sees development briefs, structural checks
and review findings, with no tools or held-out evidence. The baseline uses the
actual `Controller` guidance with fresh empty scoped experience. Candidates add
one bounded procedure; all arms share the same isolated media-tool adapter.

`freeze` records the development selection and a frozen challenger. Selection
keeps the baseline eligible and favors it on exact ties. A candidate cannot win
selection by lowering overall success or structural success, increasing
unverified outcomes, or increasing runs with observed material defects. Held-out
evaluation compares the baseline and frozen challenger on six new tasks: another
12 producer runs, even when development retained the baseline. There are 30
producer runs in total, plus one proposal call and the independent review calls.
Additional repetitions multiply the producer-run counts.

The held-out tasks use different generated source artwork, dimensions, wording
and motion requirements. Each split contains three creation and three repair
tasks, including images and short videos. Sources are generated from portable
synthetic specifications. The producer can read only its own task workspace.
It cannot inspect the suite, scorer, another run or held-out results. Candidate
authoring closes at freeze; feedback from held-out evaluation must not be used
to revise a procedure in this experiment.

Completed tasks are retained when a command is resumed. An interrupted attempt
is recorded and a new attempt uses a fresh workspace. Changes to the experiment
implementation or task suite require a new experiment. Do not edit the manifest,
procedures, receipts or generated files to repair a failed score.

## Read the results

The private output contains `pilot.json`, per-attempt workspaces and local
delivery snapshots. `report.json` and `report.md` show development and held-out
summaries, the frozen procedures, and paired held-out wins, losses and ties.
An incomplete report remains incomplete; missing or failed tasks stay visible
against the expected run count.

A successful task needs a completed producer turn, local delivery backed by an
independent pass for the exact delivered SHA-256, unchanged source bytes, and
passing structural checks. Structural checks cover disclosed properties such
as encoding, dimensions, opaque image gutters, video duration/frame rate, silent
video and temporal change. They do not establish correct text, source
fidelity, natural motion or aesthetic quality. Those rely on the separate
visual review, which is itself fallible and sampled.

Compare final success, first-review pass, unverified outcomes, review/tool calls
and elapsed time together. The material-defect count records defects found in
any review during a run, including defects subsequently repaired; it is not a
count of defective final deliveries. One default repetition on six held-out
tasks provides directional evidence, without a reliable estimate of production
performance. Both arms failing a task is a paired tie, not a success.

This synthetic pilot cannot establish a general media-quality improvement or
successful reuse on a user's real artwork. It also does not test live delivery,
external generation providers, audio quality or the production execution
boundary. A useful result is evidence for a further independently assessed
experiment on explicitly supplied real tasks. Promoting a procedure into live
scoped experience remains a separate change; none of these commands does it.
