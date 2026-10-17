# Independent quality review and scoped experience

The earlier animation was playable and contained the requested motion, but its
first version had visibly flat eyelid inserts. The producer's own frame check
missed that defect. This change adds an independent visual review and returns
actionable findings to the producer before file delivery.

## Implementation

- Decode actual snapshot-matched bytes in the existing execution worker.
- Inspect every video frame at reduced resolution for motion, then show uniform
  samples, brief motion peaks and their neighbors, broader motion context and
  magnified small changing regions. No source-specific face coordinates are used.
- Review through a separate subscription-backed thread with editing, shell,
  network and app tools disabled. A failed or incomplete review never passes.
- Return defects to the normal agent loop for autonomous repair and re-review.
  A pass applies to exact bytes and the current task; only that immutable
  snapshot is delivered, retaining the original filename.
- Keep reusable procedures in conversation-scoped persistent experience, with
  stable update keys, applicability, limitations and verified artifact receipts.

## Validation before deployment

- Standard suite: 1,142 tests, with 291 opt-in/environment tests skipped.
- Focused regressions cover same-path content mutation, changed requirements and
  references, task cancellation, no delivery on revise/unverified, review budget,
  native-image staging, scoped receipts and persistent lesson retrieval.
- Actual FFmpeg media checks cover decoding, brief localized motion among broad
  movement, magnified detail evidence, malformed input and digest mismatch.
- A real model independently rejected a synthetic title card with cropped,
  overlapping text and passed the corrected card. See [model checks](model-checks.json).
- Blind review of the real first animation initially missed the defect with
  broad contact sheets. Stronger general craft criteria alone also missed it.
  After actual magnification of automatically detected local motion regions,
  the reviewer requested repair and identified mismatched texture/shading in
  the moving eyelids with specific frame/time evidence. The task brief did not
  tell it which defect to find. Private media and prompts remain outside Git.
- The first deployed-worker probe exposed partial writes on its nonblocking
  socket: a multi-megabyte evidence response was truncated and the caller waited
  for its missing newline. The transport now drains complete responses with
  bounded backpressure handling. A real small-buffer socket regression verifies
  a 1.98 MiB response, the next request, timeout and cancellation.

## Limits

This is sampled visual assessment, not a guarantee of perfect aesthetics or
every-frame correctness. Audio is explicitly not evaluated. Unsupported file
types retain their prior delivery behavior. A passed artifact backs a saved
procedure but does not prove every sentence of that procedure or a future
render. Real producer repair, live Telegram delivery and later reuse must be
checked separately from these model and unit tests.
