# Compact sign-in deployment — 2026-10-12

Code commit `af37820f54a7c4098713c6c8f653c0dbf9e26058` was installed
through the existing reviewed execution-boundary and website-form installers.
The production checkout was fast-forwarded from `4562f4f`. No new poller,
account, browser profile, model configuration or provider grant was created.

Before the controlled stop, aggregate running work, pending inputs/deliveries
and open website leases were zero. The five existing uncertain intake records
remained unchanged and were neither replayed nor cleared. After stopping the
canonical Oak service and private form broker, both execution and broker UIDs
had zero processes before policy replacement.

The source and official pinned Brotli 1.2.0 wheel were frozen into a root-private
review bundle. The wheel hash matched the recorded candidate, and installation
used `--no-index --no-deps`. Reviewed and installed profile/config hashes match;
all 144 manifest files match. Execution scopes, firewall contents and broker
configuration are unchanged. A fresh isolation attestation passed all ten
checks. Exact nonsecret pins and the receipt timestamp are in
[deployment metadata](compact-signin-deployment.json).

The canonical gateway and worker restarted with zero restarts. The separate
broker is active with zero restarts; its authorized control health reports
`ready=true` and `website_task=true`. Public HTTPS `/login.js`, `/login.css`,
`/requests.js` and `/style.css` returned HTTP 200, `Cache-Control: no-store`, and
the exact frozen source hashes. The first urllib verification timed out reading
a response body; the subsequent bounded curl probes completed and matched all
four assets. That failed verification attempt was not counted as a pass.

The following documentation-only commit records these results without changing
the deployed product digest. Server readiness and asset hashes do not establish
owner-account authentication or physical Telegram WebView behavior. The
[varied-site report](compact-sites.md) retains the unsupported Slack/Microsoft
pages, blocked documents and incomplete provider methods alongside the real
Jev presentations and public demo logins.
