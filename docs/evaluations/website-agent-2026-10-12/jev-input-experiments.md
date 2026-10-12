# Jev input experiments — retained negative results

The production latency investigation preserved a sanitized pre-input observation
with 64 actions and one newsletter email control. Its only observed account-entry
button was labelled Sign Up. This was a fresh observation after delegation ended,
not a verified copy of the input that caused the original model abstention.
The observation remains private and outside Git; receipts contain only hashes,
counts, fixed outcomes, timing and cost. These experiments execute no browser action.

The [first comparison](jev-input-comparison.json) evaluated the deployed joint
operation/action Choice, criteria with descriptive action objects, and shorter
instructions with those same descriptive criteria. All three returned `none`, in
332, 407 and 390 ms respectively. The preceding three-call harness attempt failed
during cleanup before writing its results; those unavailable outcomes are not
reclassified as successful measurements.

The [second comparison](jev-structural-decomposition.jsonl) followed the official
[function-calling pattern](https://docs.typesafe.ai/cookbooks/function_calling):
separate operation and speculative target questions in one request. The full
projection and a compact projection both selected navigation but no target, an
incompatible pair that permits no browser execution. They took 473 and 307 ms.
A third request used synthetic existing-login, registration-only and shared-entry
controls. Two of three complete expected pairs matched; registration selected
navigation plus no target and was also non-executable. The three recorded calls
cost $0.001060122, with zero browser actions or owner inputs.

These results do not justify replacing the deployed planner with either prompt
variant or the split architecture. They demonstrate that fast inference alone
does not remove the fallback delay on this observation. Native-agent fallback
is retained. Synthetic controls are neither held-out websites nor completed
authentication, and no latency percentile or universal-compatibility claim is made.
