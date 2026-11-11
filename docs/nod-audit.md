# Nod concepts worth adapting to Oak

Code-level review, 2026-11-11. This is a focused comparison of memory,
approvals/action evidence and background work, not a penetration test or a
reproduction of Nod's published benchmarks.

Reviewed snapshots:

- Oak: [`bf3f9114600402649b4d108f91a356404d192227`](https://github.com/xliberty2008x/oak-bot/tree/bf3f9114600402649b4d108f91a356404d192227).
- Nod: [`d1461f41cd0c436feb4260703fbde67630c8e684`](https://github.com/n8n-io/nod/tree/d1461f41cd0c436feb4260703fbde67630c8e684).

## Implement now: honest memory origin and complete bounded context

### P1: model-written notes were labelled as owner-written

Oak's [`oak_memory_remember` dispatch](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/tools.py#L612-L617)
called [`remember` with its default `source='owner'`](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/memory.py#L129-L156).
Direct owner entries and model paraphrases therefore had the same label. This
loses useful evidence when a later task needs to distinguish an explicit
preference from an assistant's interpretation. It does not establish that an
unauthorized action actually occurred.

Nod [selects owner-scoped user messages](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/src/server/memory-learning.ts#L22-L25)
and [checks cited quotes against source messages](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/src/server/memory-ops.ts#L60-L114).
It also separates stated and inferred notes. The transferable idea is to retain
source evidence instead of allowing generated text to supply its own authority.

This change introduces a small, additive `memory_origins` table:

- `owner`: a new direct `/remember` or authenticated panel entry.
- `agent`: a model-tool entry, with thread/turn IDs taken from the runtime
  envelope after checking the selected conversation and active turn.
- `import`: a selected owner-supplied export. The original file label is retained,
  but imported claims are not treated as verified owner testimony.
- `legacy`: no reliable new origin record. Existing note text, source labels and
  timestamps are preserved; an old `source='owner'` is not retroactively verified.

The memory tool accepts only text, not model-supplied provenance. New origins,
notes and FTS rows share a transaction; panel retry receipts remain atomic.
Scoped deletion removes the origin too. No additional copy of owner messages,
private prompts, credentials or imported files is placed in the origin table.
Search returns creation time and origin metadata alongside existing fields.

**Limit:** origin identifies the writer/entry path, not the truth of the note,
semantic support for its claims or permission to take an external action.
Even a source-quote substring check would not prove entailment. Server-validated
message/quote evidence and conflict handling are useful next steps, not features
claimed by this patch. This is not a complete memory-injection defense.

### P1: startup context could end inside a serialized note

Oak [serialized recent notes and sliced the resulting JSON at 24,000 characters](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/controller.py#L321-L324).
A long newest note could hide later notes and truncate its own source metadata
or closing structure. Nod's [recall projection includes explicit shortening and
completeness fields](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/src/server/memory-recall.ts#L122-L175).

The new startup index preserves complete JSON and origin metadata, has an
explicit per-note `text_truncated` flag and a collection `has_more` flag, and
stays within 24,000 serialized characters. It includes at most 20 notes, with
2,000-character excerpts. Passing a note ID as the existing memory tool's query
retrieves that exact complete note from the selected conversation, even when
common wording would bury it below the keyword-search limit. This budget is
characters, not a promise about model tokens or UTF-8 bytes.

Memory search now rejects non-text/over-8,000-character queries and uses at most
80 quoted search terms, matching the existing experience-search approach.
Startup instructions explicitly treat notes as reference, not new commands or
action authorization. Those instructions are guidance, not an enforcement proof.

## Next PR: bind approvals to the exact proposed action

### P2: durable approval is not yet a unified exact-action audit record

Oak already has [durable, turn-scoped interactions and decisions](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/interaction.py).
Its custom [browser approval wrapper](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/tools.py#L473-L486)
intentionally omits private arguments and shows a generic action label. The
configured `never` option skips that wrapper; native runtime/provider controls
are separate. This is a feature gap in action evidence, not a demonstrated
end-to-end authorization bypass.

Nod [validates permission basis against owner-message evidence](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/src/server/trust-basis.ts#L95-L176)
and [claims confirmed actions under a turn lock, executes saved arguments, and
marks ambiguous outcomes uncertain](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/src/server/approvals.ts#L629-L696).

A separate Oak change should bind a private immutable action fingerprint and
observed page revision to the approval, revalidate immediately before execution,
and record approval/execution outcomes separately. Public/shared events should
continue to exclude credentials, token-bearing URLs, typed values and selectors.
Tests should cover changed arguments, stale pages, wrong conversation, timeout,
restarts and uncertain sends. Do not add another model reviewer and assume it
solves enforcement; do not weaken current browser or private-broker gates.

## Later: cheap change signals for selected monitors

### P2: reduce unnecessary model work without changing retry ownership

Oak already [claims jobs durably, waits for an idle conversation and preserves
uncertain outcomes](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/schedule.py#L85-L145).
Those protections should not be replaced with unconditional retries. Recurring
intervals currently [advance from completion time](https://github.com/xliberty2008x/oak-bot/blob/bf3f9114600402649b4d108f91a356404d192227/oak/schedule.py#L70-L77),
so they are not a calendar/cron scheduler.

Nod [skips an unchanged monitored input only after a successful prior run and
includes schedule revision in the marker](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/src/server/schedules.ts#L309-L338).
This is useful once a specific Oak monitor has an inexpensive trustworthy signal,
for example a source revision or an ETag. Keep it opt-in and invalidate the
marker after task edits, failed checks or uncertain execution. Do not introduce
generic autonomous polling or let a second scheduler replay external actions.

## Boundaries and rollout

This patch does not change the model, ChatGPT subscription authentication,
API-key policy, approval settings, private browser/CLI brokers, scheduler,
conversation ownership, UI components or delivery retry behavior. No new
service or dependency is required. It independently implements concepts in
Python rather than porting Nod's TypeScript/runtime stack.

The schema change is additive and idempotent. Back up private deployment state
before upgrading. Existing rows are not reclassified as trusted or rewritten.
A normal service restart loads the new startup context; already active native
turns do not receive a retroactive context replacement. Do not restart a live
external action just to apply this patch. A code rollback must retain the
existing state database; older code will not maintain the additional origin
metadata, so restoring trustworthy provenance after rollback requires review.

Repository tests can verify storage, dispatch and context contracts without
accessing private deployment state. They do not establish model accuracy, live
Telegram behavior, production credentials, OS isolation or provider access.

## Licensing and attribution

Conceptual sources above are from Nod by n8n GmbH at the pinned revision. No
Nod source was copied into this implementation. Nod's relevant core is
[Apache-2.0](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/LICENSE).
Any later code copying/adaptation must retain applicable notices and license
terms and identify modifications. Its [NOTICE](https://github.com/n8n-io/nod/blob/d1461f41cd0c436feb4260703fbde67630c8e684/NOTICE)
also identifies GPL-3.0-or-later local phonemizer code; do not assume every
component can be reused under Apache alone.
