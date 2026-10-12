# Bounded compressed public assets

Fresh anonymous live Microsoft and Slack pages exposed empty DOMs. Public
resource diagnostics found HTTP 200 JavaScript responses with gzip/Brotli content
encoding even though the relay requested `Accept-Encoding: identity`. The old
relay rejected every non-identity response before the scripts could execute.

The shared static-resource relay now decodes identity, gzip, zlib/raw deflate and
Brotli streams in bounded output chunks. It keeps `auto_decompress=False` so
both encoded and decoded bytes can be limited explicitly: at most 4 MiB per
resource and 24 MiB across a lease, with cumulative counters on rejected bodies.
Malformed, truncated, unsupported and trailing encodings fail closed. Supported
concatenated gzip members share the same bounds. Only fully validated bodies
are fulfilled to Chromium, without encoding or credential response headers.

DNS pins, anonymous cookie-free requests, method/MIME checks, origin scopes,
redirect restrictions, timeouts, concurrency and the irreversible private-input
freeze remain in force. This is a resource-format correction, not a new website
grant. No per-site condition was added.

## Dependency and validation

Canonical requirements now pin `Brotli==1.2.0`, from the
[official Python release](https://pypi.org/project/Brotli/1.2.0/). Its
[decoder implementation](https://github.com/google/brotli/blob/v1.2.0/python/_brotli.c)
uses `output_buffer_limit` as a growth threshold rather than an exact maximum.
The relay therefore asks for small 64 KiB chunks and independently checks every
returned chunk against its own quotas. The CPython 3.12 aarch64 candidate wheel
SHA-256 was `acec55bb7c90f1dfc476126f9711a8e81c9af7fb617409a9ee2953115343f08d`.

The four resource modules passed **64 tests, zero skips, in 2.495 seconds**.
They cover exact codecs, decoded bombs, encoded/decoded per-resource and shared
caps, invalid/truncated/trailing data, concatenation, freeze, missing Brotli,
local HTTP servers ignoring the identity request, and actual Chromium execution
of a Brotli script and gzip stylesheet. The first run exposed a mistaken decoder
drain-state assumption; that candidate failure was retained and corrected before
the final run. No production dependency or service changed during these checks.

```sh
OAK_PROVIDER_BROWSER_TESTS=1 PYTHONPATH=tests python -m unittest \
  test_website_resource_compression test_website_resources \
  test_website_resource_discovery test_website_resource_hook -q
```

[Varied-site receipts](compact-sites.md) distinguish the first failed pages from
subsequent compressed-resource regressions. Resource checks are not proof of
successful owner authentication or compatibility with all sites.
