# Production cycle recovery

The first installed background cycle did not reach independent evaluation.
All four DEV executions ended without verified delivery, and all six visual
reviews returned `review_runtime_unavailable`. The two fresh holdouts remained
unopened. This was an infrastructure failure, not a measured quality rejection.

The reviewer explicitly supplied `execution=None`, overriding the protected
profile previously bound through a keyword argument. The failure happened before
native reviewer construction. The production factory now binds that profile
positionally, permits the reviewer's omitted profile, and rejects a conflicting
profile before native execution.

The upstream search correctly returned no final candidate. Oak subsequently
attempted to construct a path from that missing selection. An empty search now
ends with an explicit, unevaluated non-admission; no candidate is registered,
no holdout is opened, and no active procedure changes.

The old DEV context also exposed a concurrent source-copy defect. Upstream's
parallel proposal builders could copy a shared image while another request
rewrote identical source bytes. Sanitized optimizer sources are now published
once and verified on reuse; consumers cannot observe a truncated rewrite.
The pinned upstream code is unchanged.

Recovery preserves the original sealed configuration, native receipts and
unopened holdouts. The trusted `close-pre-freeze` operation records an immutable
termination receipt and explicit authorization reference. A replacement requires
its own finite quota and approved DEV evidence revision, receives fresh holdouts,
and uses the current implementation. Prior DEV attempts remain available as
authentic history. An unmeasured closure is excluded from quality denominators
in the status and portable report.

Verification distinguishes:

- Local regression tests for the real reviewer interface, parallel source
  copying, immutable closure, admission guards and private export.
- One bounded native reviewer smoke through the installed protected transport.
- A replacement cycle executed by the existing production background worker,
  using the same `gpt-6.1-sol` model and `low` effort.
- After genuine independent acceptance only, a separate bounded native task
  through `Controller.submit`, with procedure selection and input insertion
  performed by the controller. An isolated validation task proves that path;
  it does not claim real Telegram delivery or further media-quality gain.

Successful deployment or a passing reviewer smoke does not establish a quality
gain. That claim requires the replacement's frozen comparison, fresh holdouts,
unchanged admission rule, and recorded automatic activation.
