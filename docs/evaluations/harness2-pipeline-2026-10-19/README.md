# Two native Harness² cycles in Oak — 2026-10-19

Both candidates were rejected. Neither achieved a strict gain on its two fresh
holdouts, and each lost a baseline success in regression. The existing procedure
remains active. These results demonstrate the improvement pipeline and its
rejection gate; they do not demonstrate improved media quality.

The engine is unmodified upstream Harness² at
[`1cfe254e8d25b15f37048c3730134686abe60335`](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335).
Its actual `run_task`, Propose–Probe–Compose, internal selection, reflection and
Library run through Oak's benchmark and subscription adapters. All native roles,
producers and reviewers use the existing ChatGPT subscription with
`gpt-6.1-sol`, low effort. No API billing or alternate model was used.

See the [component diagram, ownership table and full reproduction guide](../../harness2-improvement-pipeline.md)
and the [upstream API/authentication study](../../harness2-upstream-integration.md).

## What was measured

Each cycle has one new DEV task, two regression tasks and two fresh holdouts.
At `k=1`, upstream runs four DEV producers and four improvement-role calls
(INV, DOM, composition, reflection); selection is trivial and has no additional
selector call. Independent comparison runs both arms on the four evaluation
tasks, after candidate freeze. That is 12 producer executions per cycle,
24 across both cycles, plus eight improvement-role calls. Native reviews are
additional calls within producer execution.

There are only **four distinct fresh holdout tasks** across both cycles, and
eight distinct source tasks overall. Cycle 2's regression tasks are cycle 1's
revealed holdouts; their repeat runs are not fresh evidence. The twelve imported
historical pilot cases are registry references, not twelve additional native
executions in this experiment. The scope is synthetic layered-media creation
and repair, with source-preserving occlusion. Other media families and live
provider behavior were not measured.

Every independent arm must complete, preserve its sources and deliver an
immutable artifact. Separate native visual review and mechanical evaluation
must pass on that same delivered SHA. Visual pass alone, a high mechanical
score, and successful delivery alone are insufficient. All terminal attempts,
traces, deliveries, review bindings, scores and decisions remain in the JSON
evidence, including negative outcomes.

The admission policy was sealed before independent evaluation. Both cycles use
policy SHA `a8217e0dfaee09a336e5becb76e5374e5de39f6ba704d6fda22f304990fafa18`,
the same model, tools, thresholds, reviewer and budgets. The candidate can edit
only `AGENTS.md`, mapped to Oak procedure text. It cannot edit measurement,
hidden inputs, permissions, model or acceptance policy.

| Cycle | Independent split | Baseline joint | Candidate joint | Candidate wins / losses | Decision |
| --- | --- | --- | --- | --- | --- |
| 1 | Two revealed historical regression tasks | 1/2 | 1/2 | 1 / 1 | Reject |
| 1 | Two fresh holdouts | 1/2 | 1/2 | 1 / 1 | Reject |
| 2 | Cycle 1 holdouts, now regression | 1/2 | 1/2 | 1 / 1 | Reject |
| 2 | Two new fresh holdouts | 1/2 | 1/2 | 1 / 1 | Reject |

Equal aggregate success counts conceal lost baseline successes. All 16 independent arm
executions delivered and passed native visual review, but only eight passed the
mechanical contract as well. All 24 producers and eight improvement roles
finished; there were no original unknown/interrupted native attempts in these
two cycles. These observations do not establish the reliability of every future
run or the correctness of a native visual reviewer in general.

The following scores measure source properties and are secondary diagnostics,
not aesthetic-quality scores. Time is summed producer elapsed time for the two
tasks in each row, not wall-clock experiment duration.

| Cycle | Split | Arm | Joint | Mean mechanical score | Seconds | Tools | Reviews |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Regression | Baseline | 1/2 | 0.868936 | 185.543 | 37 | 2 |
| 1 | Regression | Candidate | 1/2 | 0.863053 | 198.354 | 39 | 2 |
| 1 | Fresh holdout | Baseline | 1/2 | 0.867990 | 210.745 | 43 | 2 |
| 1 | Fresh holdout | Candidate | 1/2 | 0.840159 | 165.819 | 38 | 2 |
| 2 | Regression | Baseline | 1/2 | 0.863365 | 191.474 | 40 | 2 |
| 2 | Regression | Candidate | 1/2 | 0.880703 | 164.188 | 35 | 2 |
| 2 | Fresh holdout | Baseline | 1/2 | 0.884433 | 194.946 | 38 | 2 |
| 2 | Fresh holdout | Candidate | 1/2 | 0.881386 | 181.665 | 37 | 2 |

Across search and independent comparison, producers used 2,210.588 summed
seconds, 452 tool calls and 24 recorded reviews. Improvement roles used 366.594
summed seconds and 238 tool calls. The separate activation follow-ups are
excluded from these totals and reported in their own evidence.

## Experience transfer and protocol disclosure

Both actual native reflections returned `NO_ENTRY`. Upstream Library stayed at
zero entries in both cycles. No fabricated positive lesson was inserted.

After cycle 1 and before sealing cycle 2, Oak added a bounded scoped
`attempt_history` context containing the original four DEV role responses from
cycle 1. Their original SHAs are verified. This is an explicitly separate Oak
adapter addition: upstream's selective Library and prompts are unchanged.
Independent grades, decisions and regression/holdout inputs are excluded.
Cycle 2 received that history, and both its INV and DOM roles actually read the source
evidence file. The evidence establishes transfer and a recorded read, not that
the model fully understood every prior response or learned a successful rule.

Cycle 2 history content SHA is
`4e55da44b02fa161a19669cf6af51e5a536c281a56058fd5d1f0fa20f1a9c8eb`.
The integration change makes this a sequential pipeline demonstration, not a
controlled causal comparison of two identical optimizer configurations.

The exact publicly retained source trees for the native cycles are
[cycle 1](https://github.com/xliberty2008x/oak-bot/tree/cb34f86979a2d83b5baf5517184fc1090a95ca47)
and [cycle 2](https://github.com/xliberty2008x/oak-bot/tree/741498a0d0f888823de3e96fe6e4a2b4ad91acce).
Both sealed the same media adapter SHA
`3640d9062882425dc36f5f2a6bb9198a8e4cf85b2930cc025134f97bcaa86f25`
and bridge SHA
`a4d61e5bc49ce94b5bf5e0e0bee52e7f07a15869eb4f3267fdc91567d63dadda`.
The full implementation file hashes, freeze times and source commitments are
retained per cycle in the export.

## Deployment and persistence checks

The separate controlled demonstration temporarily admits a labeled benign
fixture B, blocks a rejected fixture C, runs a native Oak task with B, closes and
reopens the version store, rolls back to the original A, and runs another native
task with A. Administrative fixture admission is not a model-measured quality
win. These follow-ups test Controller application provenance and monitoring;
they are separate from the independent comparison and are not mechanically
graded as additional holdouts. The original active pointer is restored.

Both native follow-ups completed and delivered, with bound version, procedure,
context, input and actual native turn IDs. B used 84.101 seconds and 20 tool
calls; restored A used 91.153 seconds and 17 tool calls. There were two initiated
follow-ups and no unknown interruptions. Their preserved delivery/review
receipts are in the full export and independently checked in
[`activation-independent-qa.json`](activation-independent-qa.json).

Stop/resume and fresh-process restart checks preserve the queue, versions,
decisions, native receipts and active pointer without new native reservations.
Focused tests separately cover crash recovery, bounded retries, failed terminal
results, source tampering, worker exclusion, cross-scope isolation and gate
rejection. Cached replay is not counted as another model execution.

The first process check preserved 16 native receipts and 141 budget operations
after cycle 1 ([proof](restart-proof.json)). The final check preserved 34 native
receipts, 270 budget operations, all scoped application records and the restored
active version after both cycles and the two follow-ups
([proof](final-restart-proof.json)). Both created zero new native reservations.

After these native runs, the final source additionally hardens three local
edge cases: completing receipt validation after a crash without replaying the
producer, rejecting explicit rollback to quarantined or nonancestor versions,
and validating configuration without reading another conversation at Controller
initialization. Focused local tests cover these changes. The native quality
evidence remains bound to its original source seals; it was not rescored or
silently attributed to the later code. A future cycle seals the new source.

The final focused run passed **172 tests in 65.455 seconds**, including the
existing native-runtime and durable Controller state checks
([command, environment and source hashes](local-validation.json)). This local
suite uses fixtures for runtime transport; its model-like event labels are not
additional native executions. Actual model evidence is the separately retained
subscription-backed runs above. The earlier pilot worktree remains clean at
`bdbab33f4653c262f70ff8b6d8b202f1be235e45`.

## Evidence and limits

[`native-two-cycle-results.json`](native-two-cycle-results.json) is a portable export of synthetic public
evidence, with original file SHAs and a separate portable content commitment.
Private databases, source workspaces, videos and authentication remain outside
Git. The exporter fails closed for non-public cases. JSON audits check preserved
bindings and decisions; they do not themselves execute models or judge pixels.
The independent final JSON audit passed
[122 checks](two-cycle-independent-qa.json); the deployment audit passed
[78 checks](activation-independent-qa.json). The earlier
[cycle 1 audit](cycle1-independent-qa.json) is preserved separately.
The export's raw SHA is
`43edba1933f82b19619e0fc7a9a5d4d899fc8eb545c7502b078ac3c463a1bb73`;
its portable content SHA is
`7b0855b78707c4a0d4f52f877b3cde2da2d8184145b58b516cdd2175bcad4334`.

Selected authored construction controls passed valid encoded outputs and
rejected semantic negatives before native search in both cycles. Their lossless
frames and encoded-file hashes are retained separately. These local controls
validate construction, not model aesthetics. Earlier superseded construction
attempts remain private: one preliminary codec conversion was unverified after
its unchanged timeout, and an initialization preflight was stopped before any
model execution. Neither was silently converted into native quality evidence.

Native execution was serialized to fit the existing host. Existing swap was
left in place; this task did not add swap or change host memory policy. Dollar
cost is unknown for subscription execution. Elapsed time, tool calls and review
counts are reported as cost proxies. Upstream's compatibility value
`cost_usd=0` is not a measured zero cost. Summed role elapsed time can include
lock waiting and must not be added to producer elapsed time as wall-clock cost.

The integration demonstrates local tests/codec controls and real
subscription-backed Oak execution. It does not verify live YouTube uploads,
Telegram delivery, external media generation providers or production rollout.
The earlier upload diagnosis remains a separate change in
[PR #105](https://github.com/xliberty2008x/oak-bot/pull/105).
