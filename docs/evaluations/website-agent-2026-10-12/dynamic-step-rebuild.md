# Dynamic step reconstruction through the private renderer

Three local Chromium integration tests passed in **26.256 seconds**, without
skips, during the compact-login change. They exercise the shipped private HTML,
CSS and JavaScript in a separate browser and a cross-origin iframe, real broker
HTTP endpoints, the MCP client/server, WebsiteTask, StepPlanner and the native
scene collector/compiler.

Provider documents are synthetic and every provider request is intercepted.
The private HTTPS browser requests are forwarded to the local broker test
server. Browser startup is replaced by this local fixture; production network
configuration, account profiles and credentials are not used. Typed model
responses are mocked. This is execution/renderer evidence, not a fresh held-out
model evaluation, public-site compatibility or successful authentication.

`tests/test_website_dynamic_steps.py` verifies:

- A method chooser becomes an email step, a temporarily empty loading state,
  a password step, an OTP step, an explicit OTP error with a fresh empty input,
  and an action-only continuation. The loading fixture hydrates after 450 ms.
- Stable polling preserves the identifier draft. Replaced steps remove previous
  fields; new password/code fields are empty. Every observed alternative is
  retained, including the explicitly unavailable external method.
- Five accepted private actions produce exactly five local site events. The
  method selection sends no values. Synthetic identifier/password/code canaries
  are absent from MCP messages, planner requests, parent-frame messages,
  private snapshots and safe diagnostics.
- A separate open-shadow-root fixture replaces an outstanding input. The new
  revision removes the old draft and field reference. An obsolete private
  submission receives `stale_form` without a site click or lease failure.
- A 316-character native text username without `@` submits through the private
  renderer even when Jev selects EmailInput. The following native email step
  retains email validation even when the model selects TextInput. Both scene
  compilers preserve observed native input constraints.
- None of the fixtures is classified as authenticated. Private cancellation confirms
  browser cleanup.

The first harness run initialized MCP before attachment and was rejected by the
existing broker guard. After correcting that test ordering, all reconstruction
assertions passed; its last cancellation selector still used the old button
copy. The final test uses the stable cancel element ID. These were harness
corrections, not product failures or model retries. The original run logs remain
outside Git with the task artifacts.

Run from the source root with the installed browser environment:

```sh
OAK_PROVIDER_BROWSER_TESTS=1 PYTHONPATH=tests python -m unittest test_website_dynamic_steps -q
```

The passing test-file SHA-256 was
`470b3f65cc3d082c47cd9352b9dab8aea298e98336b523b945e83449b8e03d44`.
The existing provider/network and actual-model evidence remains separate.
