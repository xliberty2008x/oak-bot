# Scoped TikTok static resources

The public `https://www.tiktok.com/login` page needs executable assets outside
its navigation origin. Blocking them left its login-method chooser inert.
`website_resources.py` now supports these exact observed dependencies:

| Exact HTTPS host | Allowed resource types |
| --- | --- |
| `sf16-website-login.neutral.tiktokcdn-eu.com` | script |
| `sf16-website-login.neutral.ttwstatic.com` | script, stylesheet, font |

This is **disabled by default**. An explicit boolean `tiktok_static_resources: true`
in the reviewed broker/setup configuration activates it only for generic leases
for exactly `https://www.tiktok.com`. Absent/false starts no static-fetch client
or extra DNS resolution. Named-provider shortcuts do not opt in.
Other sites, named-provider policies, navigation, form actions, cross-origin
fetch/XHR, frames and WebSockets keep their existing destination restrictions.
It does not authorize another SSO or credential recipient.

Downloads use a separate aiohttp client, not the browser's cookie jar or
`route.fetch`. It resolves both exact hosts to public addresses and pins those
addresses while retaining hostname TLS verification. Every redirect is checked
against the same host/type rules. GET/HEAD only; HTTPS443, no query, fragment,
embedded credentials, request body, cookies, authorization or referring-page URL.
Only fixed transport headers are sent. Browser request headers are never copied.

The fetcher bounds a resource to 4 MiB, aggregate received content to 24 MiB,
requests including redirect hops to 96, redirects to three and each resource
operation to ten seconds. At most six downloads run concurrently. Response
status/content type/content encoding are checked; only safe content/CORS headers
reach `route.fulfill`. Set-Cookie and arbitrary upstream headers are discarded.
The client, DNS pins and private URL set are closed with the browser lease.

Before any human field values are dispatched, the allowed paths freeze to exact
URLs already fetched anonymously. Newly constructed paths are denied as well as
queries: a page must not encode entered values into a new CDN URL. Late JavaScript
chunks first requested after input can therefore remain unsupported. Empty public
method-chooser actions can load their dependencies before that freeze.

Downloaded scripts still execute with the TikTok page's DOM access. These
controls constrain downloads; they do not sandbox third-party code or establish
that a provider is trustworthy.

## Evidence and limits

On 2026-10-11 a fresh anonymous Chromium probe with the integrated helper loaded
30 scripts and three stylesheets from these hosts. The actual private adapter's
empty-field chooser action changed `/login` to `/login/phone-or-email`; a public
email-method DOM click reached `/login/phone-or-email/email`. No credentials or
real model calls were used. The email page exposed text/password controls inside
a default-GET JavaScript form. A subsequent anonymous probe with the updated
script-only collector and an explicitly mocked selector returned both private
fields and three actions. There was no form input, real Jev call or authentication.
Asset loading and collection alone are **not successful form login**.

Nineteen focused transport tests cover pins, redirects, headers, methods/types,
bounds, cancellation, path freeze and cleanup. Browser lifecycle hooks have
separate checks. These are distinct from live model, mobile UI and owner login
acceptance; record those outcomes explicitly.

This began as the proposal in Hub report5146. Report5160 records the owner's
specific approval of the two hosts/types above; it does not approve additional
credential destinations. The configuration remains default-off in the portable
installer. Source implementation, deployment activation and successful
authentication remain separate facts. Cross-origin login APIs, regional redirects
and challenges are not proven compatible by these anonymous probes.

## Generic exact-origin policies

An optional `static_resources` map reuses this transport for explicitly selected
page-origin/resource-host/type tuples. It adds no hosts by default and supplies
only the current generic lease's exact-origin policy. The separate TikTok boolean
still selects its original two hosts/types. Neither feature opts named provider
shortcuts into extra resource loading.

The proposed [six-origin experiment](evaluations/website-static-resources-2026-10-12/README.md)
contains seven exact CDN hosts and script/stylesheet types only. It remains
inactive pending the network-scope decision requested through Hub. Confirmed
denied dependencies justify an experiment but do not prove that loading them
restores a usable form. Existing HTTP error pages are outside that experiment.
