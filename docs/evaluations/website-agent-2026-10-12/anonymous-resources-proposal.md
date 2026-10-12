# Candidate: anonymous frontend resource discovery

The Dropbox diagnosis establishes that the current explicit host policy can
leave a successful HTML response without executable frontend code. A generic
resource loader is implemented and tested; production activation remains off
pending the coordinator's owner-approval requirement in Hub report 5259.

The concrete operator setting is `anonymous_static_resources: true` in the
immutable website broker configuration. Omitted or false keeps the existing
exact approved resource policies. It affects native-agent website workspaces
only; the model cannot enable it, and legacy provider forms keep their policy.

The requested scope is public HTTPS origins referenced by the selected website
for scripts, stylesheets and fonts, discovered before private input. It is not
a preselected per-site domain list. A lease can attempt at most eight hosts,
including failed DNS lookups; all DNS answers must be public and one address
is pinned per host. GET/HEAD only, no request body, no URL query or fragment,
no browser cookies, authorization or referrer. Existing limits remain 96
requests, 4 MiB per resource, 24 MiB per lease, three redirects and ten seconds
per request. MIME types are checked. DNS, redirects and in-flight completion
are checked against the monotonic private-input freeze.

Loaded scripts execute as code within the selected site's page and persist
into later private input. They can inspect those DOM inputs. Anonymous download
transport is **not** a JavaScript sandbox or proof of script trust. Before the
first private values mapping, the relay freezes to exact anonymously completed
URLs. New hosts and paths remain blocked afterward. No authentication API,
XHR/fetch, navigation or credential-destination grant is added; the separately
approved OpenRouter Clerk policy remains unchanged.

Approval is requested for this general scope, not an additional Dropbox-only
exception. Without approval the flag stays off and the observed Dropbox
resource failure remains unresolved. Action-only continuation and page-readiness
wake fixes do not depend on this setting and can ship under existing scopes.

Validation: 39 resource tests (25 legacy and 14 discovery), 19 browser adapter
tests, and 76 broker/gateway/configuration tests passed. Independent review found
no blocking transport, activation or continuation defect. No production live
success is claimed for this disabled candidate.
