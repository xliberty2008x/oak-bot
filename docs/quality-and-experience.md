# Artifact quality and reusable experience

With `quality_review: true`, Oak reviews image/video candidates before the normal
file-delivery tool can publish them. `oak_review_artifact` supplies the requested
result and optional actual source files. The worker copies and hashes the file,
decodes evidence, and a separate subscription-backed model thread assesses it
against the owner's current task. The reviewer has no producer conversation or
editing/delivery tools. It returns `pass`, `revise`, or `unverified` with evidence.

The producing agent receives defects as a tool result and repairs the output in
its normal loop. Changed bytes require a new review. Up to four independent
reviews are allowed in one task; failure or an exhausted budget never becomes a
pass. The final delivery uses the exact gateway-owned snapshot that passed,
preserving the original filename. Review is cancelled with its task. This is an
automated quality aid, not a guarantee of perfect aesthetics or all-frame/audio
coverage. Unsupported formats retain their existing delivery behavior.

`oak_experience` stores reusable procedures separately from owner preferences,
scoped to the selected conversation. A stable key updates an existing method.
Each lesson records applicability, reusable script paths, known mistakes,
limitations, revision, and optional trusted review receipts. A `proven` label
means an associated artifact passed review; it does not independently validate
every sentence of the agent-written procedure or guarantee success on new input.
Saving without verified evidence marks the current lesson `unverified`.

Oak searches experience on each new input and sees a bounded lesson index when
loading its thread. Lessons persist in SQLite across restarts and thread resets.
They do not copy private inputs to another conversation or to Git. Future tasks
must inspect reusable code and adapt source-dependent parameters rather than
blindly reuse geometry from the original artifact.
