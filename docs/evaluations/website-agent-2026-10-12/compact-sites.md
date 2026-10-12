# Diverse live sites and dynamic rebuilding — 2026-10-12

This evaluation deliberately excludes OpenRouter. It tests actual anonymous
public pages, then separately checks real Jev proposals and public demo-account
submissions. It does **not** establish successful owner-account authentication,
Telegram interaction, native GPT/MCP control, or universal website support.
The compact private renderer has a separate real-browser acceptance record.

## Frozen first sample

Before network execution, the independent evaluator froze these seven sites:
GitHub, Wikipedia, WordPress.com, Slack, Microsoft Live, Dropbox and Spotify.
No site was removed after seeing its result. Each trial used a new browser
context, the production `WebsiteAgentForms` adapter and its public-DNS-pinned
navigation/anonymous-resource policies. No account values were submitted on
these seven sites. The only attempted action in this first sample was a
value-free GitHub recovery link. Model calls were zero.

The immutable code was exported from `4562f4f`; its Oak-file manifest digest is
`dc64613dcc8084963847f5d5d0572061117720af7c004339b9745efab9043fbb`.
The full [manifest](compact-sites-manifest.json) was written before the run.
[First receipt](compact-sites-first.jsonl), SHA-256
`98efbd06bee9dad6e43587fc495d8112a3155851c959863dc0598065e2b4c5b0`.

| Site | First observed result | Further checks |
| --- | --- | --- |
| GitHub | Username/password discovered in 2.781 s. Recovery presentation selected a too-small password-only surface; its click was rejected as stale before dispatch. | After hydration, a fresh explicit recovery click was accepted once; the next main document returned HTTP 403 and a CAPTCHA iframe. Real Jev independently selected the complete two-field login region and six actions. |
| Wikipedia | Initial cross-origin redirect waited for private approval; no grant was inferred. | Explicit simulated private approval for `auth.wikimedia.org` exposed username/password/keep-signed-in. A recovery click changed the page, but the follow-up did not establish a correct recovery form. |
| WordPress.com | `provider_blocked`; no controls exposed. | Main document returned HTTP 403. No CAPTCHA or rejection bypass was attempted. |
| Slack | No controls/actions after ten reads. | Actual DOM was empty; HTTP 200 scripts were rejected because the CDN returned Brotli despite requesting identity encoding. |
| Microsoft Live | No controls/actions after ten reads. | Actual DOM was empty; HTTP 200 scripts were rejected because the CDN returned gzip despite requesting identity encoding. |
| Dropbox | Email form presented in 5.043 s. Predeclared value-free recovery/method action unavailable. | Real Jev selected the email region with Continue, Apple and Microsoft alternatives. Google was not a visible collected action; scripts/iframes had failed. |
| Spotify | Identifier form presented in 1.792 s. Predeclared phone/recovery actions unavailable. | Real Jev selected seven actions, including Google/Facebook/Apple. Its EmailInput choice for a native text control exposed a validation mismatch subsequently corrected in source. |

All seven first trials reported cleanup and unchanged frozen code. These are
three presented selected surfaces out of seven, **not** seven successful logins.
The deterministic first harness's region choice is not evidence of model
judgment or complete task-relevant UI. The later real Jev results below provide
that separate judgment evidence for the three available pages.

## Retained diagnostics and failures

[Diagnostic v2](compact-sites-diagnostic-v2.jsonl), SHA-256
`e223efea2a2874ac44bd435a2c9ae110b71d2a6cbcc8f02da6b48c4177eae9cf`,
retained the same seven sites and frozen code. It waited for hydration and
changed only the evaluator's surface selection. GitHub's accepted recovery
click led to HTTP 403, not a successful second login step. The Wikipedia
metadata probe inadvertently waited on the paused document; its owned test
browser was explicitly terminated to unblock that diagnostic, then normal
adapter cleanup completed. Its cleanup flag therefore does not establish a
naturally bounded paused-document metadata probe.

[Resource-only metadata](compact-sites-resources.jsonl), SHA-256
`9a9d1cb563c1d20e91b417c859ac87f8f975df2d355ddb28449daa5f4525e3e9`,
confirmed the generic compression issue without changing resource policy. The
record contains host, status, MIME type, content encoding and length only;
no raw request URLs, cookie/auth headers or response bodies. Microsoft returned
three gzip JavaScript assets; Slack returned Brotli JavaScript assets from its
two CDN hosts. Both pages had HTTP 200 main documents and zero collected
controls, actions and overflow. Asset bytes existed; the browser had no usable
login DOM. This is an extraction-input failure, not a Jev reasoning failure.

[Wikipedia v3](compact-sites-wikipedia-v3.jsonl) retains the first consent
follow-up's `navigation_pending` failure after one accepted click. [Wikipedia
v4](compact-sites-wikipedia-v4.jsonl) waits for observation readiness without
repeating that click. The revision changed and a new private scene compiled,
but it contained menu toggles rather than expected recovery inputs. It is
**not** counted as a correct recovery-step pass. Its deterministic surface
choice also included unrelated page navigation; it is not native/Jev quality
acceptance. Source and browser policy were unchanged in both follow-ups.

## Actual Jev judgments on three live pages

The supported subset was frozen before this separate model run: GitHub,
Dropbox and Spotify. The installed integration loaded the existing private
configuration through `step_planner_factory` as the broker user; no API key was
copied, printed or added to evidence. Each fresh browser produced a sanitized
observation, then actual `StepPlanner.plan` chose the step, surface and
components. The adapter independently validated/presented its plan. No
site actions or input were executed in this three-site run.

[Real Jev receipt](compact-sites-real-jev.jsonl), SHA-256
`c756494ed5424950cf83b01a26f5460d77284e5fc95691c57fb23f61300a7e74`.

| Site | Actual model-selected private presentation | Model calls / proposal time |
| --- | --- | --- |
| GitHub | Native text username + password; six actions: recovery, Sign in, Google, Apple, account creation and passkey. Unavailable methods remained disabled. | 3 / 1.120 s |
| Dropbox | Email; Continue, Apple and Microsoft alternatives. | 3 / 1.077 s |
| Spotify | One identifier; Continue, Google, Facebook, Apple, Sign up and two policy links. Native text was presented as EmailInput before the later native-validation correction. | 3 / 1.170 s |

Every selected surface retained all of its observed controls/actions; each
private form had completion disabled. This is not a claim that every available
provider method could finish. Nine actual requests used
`typesafe/jev-1.13-20260917`, 288–484 ms per request, 3.310 s summed request time,
known cost $0.001694112, zero unknown-cost calls. The three proposals were
presented and all three browsers cleaned up. The fixed four-second hydration
wait belongs to this diagnostic harness and is not product latency.

## Actual public demo logins and a post-input model rebuild

[Public demo receipt](compact-sites-public-demo.jsonl) records SauceDemo and
ExpandTesting, separate from the seven popular-site sample. These sites publish
accounts explicitly for automated testing; no owner account, user browser
profile, private token or external account credential was used. The
[ExpandTesting login page](https://practice.expandtesting.com/login) explicitly
provides its demo account and expected login/logout behavior.

Each trial accepted exactly one private demo login submission, retained the
same browser context, changed the native revision, removed the password step
and rebuilt a private presentation from the new page. SauceDemo exposed product
actions and a native Select; ExpandTesting exposed Logout. Both receipts report
credential canaries absent from sanitized model observation/diagnostics and
cleanup. These two checks are actual website submissions with deterministic
harness plans, **not** native or Jev controller acceptance. The generic adapter
still does not issue a provider-backed `authenticated` verdict.

[Post-input SauceDemo receipt](compact-sites-checkout-real-jev.jsonl), SHA-256
`f4e077e42e585cb5f47f7a2f729af817c64bce3776c17e06097bf2dc4d9ce68f`, extends a fresh demo login through Cart and Checkout using two
explicit value-free private clicks. The native scene changed after each step:
login text/password → inventory Select → cart without inputs → checkout with
three text inputs. Actual Jev then selected and presented that final observed
three-field/two-action checkout surface after private input. All selected
members were retained; the credential canaries were absent from observation,
proposal and diagnostics. No checkout details were submitted and no order was
placed. Both checkout actions were native submits and thus retained their
native same-owner field bindings; Cancel behavior was not exercised.

This final judgment used three actual requests, 300–437 ms, proposal 1.072 s,
known cost $0.000325374. Together with the three-page run above, **12 paid
requests**, known cost **$0.002019486**, zero unknown-cost calls. Model quality,
private browser dispatch and compact-renderer checks remain separate layers.

## Frozen candidate compression/native-validation follow-up

The same seven-site list was retained for the source candidate, with the generic
bounded gzip/deflate/Brotli relay and native-input validation correction. No
paid calls were added. The code was copied into an immutable evaluation tree;
its digest is `38440628e9cec102340a11b6a35e73e9151be93354cd9efccd6c837ea7efaf03`, with resource-module SHA-256
`b7c2ae6e5947750d0928f78f9e60c46658dcbf4ac20d02b85b9818174a68d8a5`.
Brotli 1.2.0 was supplied from a task-local dependency directory, not silently
installed into the production runtime during testing.
[Candidate manifest](compact-sites-compression-manifest.json),
[complete seven-site follow-up receipt](compact-sites-compression-v5.jsonl),
SHA-256 `eb8b336af34818869bb02613611387f7c399c0a06f4558e1d5243a99cae3fb2f`.

| Site | Candidate result |
| --- | --- |
| GitHub | Username/password presentation retained; one fresh recovery click accepted, then the service returned a blocked document. |
| Wikipedia | Login form appeared after exact private origin approval; the recovery click requested another origin approval, which remained pending. No blanket grant was added. |
| WordPress.com | Main document still blocked. |
| Slack | Compressed resources were now downloaded/decoded, but the observed page still had zero controls/actions after ten reads. No successful login-form claim. |
| Microsoft Live | Three compressed JavaScript resources completed, but the observed page still had zero controls/actions after ten reads. No successful login-form claim. |
| Dropbox | Native email form retained. |
| Spotify | Native text form retained; the compiler now preserves native validation independently of the model's presentation component. |

A bounded [post-compression diagnostic](compact-sites-compression-diagnostic.jsonl)
retains sanitized public runtime errors: Microsoft reported “Failed to load
BrsConfig file”; Slack reported a failed CDN asset. Required external
configuration fetches, failed larger assets and runtime boot dependencies were
not granted additional authority or bypassed to make this result look green.
The diagnostic used no paid model calls, no private values and cleaned up both
contexts. Exact remaining root causes are not fully established by these errors.

All seven contexts cleaned up and frozen code stayed unchanged. This follow-up
proves the generic transport/validation corrections did not remove the three
previously available forms. It does not establish that the two blank providers
are supported. The deterministic presentation harness still does not replace
native/Jev semantic region choice; the real-model receipts above retain that
separate evidence. Current real-browser source tests cover compressed asset
execution and native text/email semantics; these live failures remain visible.

## Reproduction

The [portable harnesses](compact-sites-harnesses/) use the existing runtime and
standard library; they are evidence utilities, not a second testing framework.
The recorded originals remain in the task evidence directory. Portable copies
replace only machine-specific code/config locations and make the code commit
label explicit. They preserve the site list, fixed diagnostic waits, action
restrictions and failure behavior. They intentionally do not turn a rerun into
a new first trial or hide failures.

From a clean exported source tree, copy the desired harness to a fresh run
directory and export `oak/` into its sibling `code/` directory. For example:

```sh
mkdir -p "$RUN_DIR/code"
git archive "$REVIEWED_COMMIT" oak | tar -x -C "$RUN_DIR/code"
cp docs/evaluations/website-agent-2026-10-12/compact-sites-harnesses/discovery.py "$RUN_DIR/run.py"
OAK_EVAL_BASE_COMMIT="$REVIEWED_COMMIT" python "$RUN_DIR/run.py" --freeze
python "$RUN_DIR/run.py" --run
```

Use the installed Playwright browser path when it is outside the default cache.
The discovery harness refuses to overwrite its receipt. The model harnesses
must execute as the user authorized to read the private composer configuration;
set `OAK_EVAL_JEV_CONFIG` to that private configuration file. Its schema is the
existing `backend/model/key_file/max_calls` contract. The existing factory reads
the API key internally; do not put a key or private path in a public report.
`real-jev.py` reserves at most twelve paid attempts across its three sites;
`checkout-real-jev.py` caps its separate follow-up at three. Combining recorded
procedures requires accounting for the aggregate paid budget explicitly.
Public-demo credentials in the example utility are intentionally published
training credentials, not portable user/account state.

Owner-account login and physical Telegram WebView acceptance remain unverified
by this evaluation. Real providers may reject the hosted browser, require
unsupported embedded challenges/popups, or depend on credentialed external APIs
outside the current grants. Such failures must remain visible; an empty page or
an accepted click is not proof of login.
