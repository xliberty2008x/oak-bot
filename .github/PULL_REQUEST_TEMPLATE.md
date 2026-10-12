## Change

Describe the concrete problem and resulting behavior. Note material limits.

## Validation

List checks actually run and results. Distinguish unit/fixture, browser, model,
live Telegram and provider checks; identify checks not run.

## UI, design or dependency changes

Mark this section N/A if unrelated. Otherwise:

- [ ] Read DESIGN.md; reused existing components and Oak styles, or explained the specific gap.
- [ ] For stack/dependency changes: compared upstream options, recorded exact pins/licence and catalogue/CSP implications, and updated DESIGN.md in this PR. Any new custom parser/state/binding/validation engine has a documented need for review.
- [ ] Preserved the component allowlist and private human-input boundary; secrets do not enter ordinary model/action data, AG-UI or logs, including through `sendDataModel`.
- [ ] Reported applicable acceptance-matrix results, including focus/validation, mobile keyboard, dark mode, cancel/reopen, replay and unknown components; explicitly marked untested or unrelated rows.
