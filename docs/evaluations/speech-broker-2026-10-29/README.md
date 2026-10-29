# Trusted speech broker validation

Implementation base: `42aa4acb277964314a0f6e2796a2a30dd19bd92a`.
The optional gateway adapter and separately provisioned broker do not change
the LLM provider, selected model, Piper TTS or existing durable attachments.

| Check | Result |
| --- | --- |
| Focused gateway regression suite | 165 tests passed in 10.130 seconds |
| New speech client tests | 15 tests passed; included in the focused suite |
| Final broker offline suite | 25 tests passed in 3.225 seconds; includes absolute socket deadline and independent primary readiness regressions |
| Local audio decoding | Real FFmpeg normalization and over-duration rejection passed on generated WAV fixtures |
| Cancellation | Active decoder, HTTP request and executor snapshot stop and clean up in tests |
| Authorization/state | Owner-only automatic voice flow, duplicate update suppression, snapshot reuse, selected model and conversation upgrade/recovery tests passed |
| Compile and diff checks | Passed |
| Paid provider/model check by these tests | Not performed; no credentials are read and no provider is contacted |
| Live Telegram check by these tests | Not performed; no poller is started |

Fixture requests use an owner-only local Unix socket and generated audio. They
verify primary and fallback result shapes, explicit Ukrainian language, safe
provider errors, malformed/oversized replies, no redirects/retries, input limits,
single-job admission and cleanup. These tests establish protocol and lifecycle
behavior, not recognition quality or provider-account readiness.

The focused suite includes tools, Telegram, Telegram UX, transfer, persistent
state, execution boundary, recovery and runtime tests. An existing execution
test failed on the unchanged baseline because its process mock lacked stdout
after runtime shutdown began draining that stream. Only that test fixture was
completed with an empty async stdout; runtime shutdown behavior was not changed.

No recordings, private transcripts, persona text, credentials or generated
credential-transfer artifacts are included in this evaluation record.

## Separate integration checks

The operator subsequently verified the paid primary with explicitly selected
public Ukrainian corpus audio. Three recordings had 3 word errors across 43
reference words (6.98% WER). A 7.74-second file took 2.443 seconds and a
60-second public-audio fixture took 4.098 seconds. These are provider checks,
not child-speech or live Telegram acceptance.

The actual new gateway client, running under the gateway account, decoded a
public OGG/Opus file and used the private broker: 7.74 seconds of audio took
2.233 seconds; temporary audio cleanup succeeded. A deliberately injected
primary network failure then reached the real local NeMo fallback in 22.053
seconds with fixed reason `network_error`. No private history was selected.

The OpenAI key was imported by the owner through hidden SSH terminal input
into the broker's private 0600 environment file. It is absent from source,
gateway/runtime configuration, the execution worker and these test artifacts.
