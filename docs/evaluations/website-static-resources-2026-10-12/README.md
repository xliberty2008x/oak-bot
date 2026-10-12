# Inactive generic static-resource proposal

`candidate-policy.json` is a concrete configuration proposal, not an active
deployment or an authentication result. It selects seven exact CDN hosts for
six exact page origins, with script and stylesheet resources only. Its SHA-256 is
recorded in the Hub request after the implementation and checks are frozen.

The anonymous diagnostics returned HTTP200 for all six page origins. Slack,
Dropbox, Booking.com, Atlassian and Twitch exposed no groups; Zoom exposed only
action groups. The receipts confirmed denied resources from the listed hosts.
They did not establish that allowing those resources restores forms, and did not
retain resource paths, query strings, MIME types or sizes. An authorized experiment
must keep failures in its denominator and measure loading, extraction, real Jev
selection and authentication separately.

The implementation uses one reusable, explicitly configured origin-to-host/type
policy with the existing credential-free static transport. It adds no per-site
forms, selectors or model instructions. An absent policy adds no hosts. The
existing separately approved two-host TikTok boolean retains its exact scope.

Each lease uses only its exact origin's policy. Downloads retain verified
HTTPS443, public DNS validation and pinning, a separate cookie-free client,
GET/HEAD without body or browser headers, no query/fragment, MIME checks, four
MiB per resource, 24 MiB total, 96 requests including redirects, three redirects,
ten-second resource operations and six concurrent downloads. Before human values,
the allowed URL set freezes to completed anonymous downloads. Cross-origin
navigation, credential destinations, XHR/fetch, frames and WebSockets retain their
existing restrictions. Downloaded scripts execute with the page's DOM access.

Deferred: additional Zoom scripts, Booking.com's second script origin,
consent/tag services, images, fonts, miscellaneous/beacon traffic and challenge
services. HTTP403/404/429 sites are not included; static loading cannot establish
a fix for their main-document failures. Notion's redirect and other unresolved
failures are outside this proposal.

Hub report5160 records approval of the two TikTok hosts only. Activation of this
additional configuration requires a separate network-scope decision through Hub.
The generic form engine and current production configuration continue to work
while that decision is pending. Owner credentials and session material remain in
the private form and must never be supplied through Hub.

The inactive implementation passed all **550 tests, 0 skipped, in 176.642
seconds**, including Chromium fixtures. Independent review found no blocking
issue. Configuration is bounded to16 origins, eight hosts per origin,32 unique
hosts and8 KiB normalized JSON. `validation.json` pins the tested production
source files. These checks do not activate any candidate policy or prove loading
compatibility with a live site.
