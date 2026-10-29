# Oak trusted speech-to-text broker

This standalone broker is separate from the Oak gateway, its model/executor,
and the existing Kira/Nemotron services. It exposes HTTP only through the
systemd socket `/run/oak-stt/asr.sock`; the socket belongs to `box`, mode 0600,
under a root-owned traversable 0755 directory. No TCP listener is opened.

`POST /v1/audio/transcriptions` accepts multipart `file`, `language=uk-UA`,
and `response_format=json`. Audio must already be normalized locally to mono
16 kHz PCM16 WAV, at most 60 seconds and a 4 MiB multipart request. One
inference is admitted, with no waiting inference queue; overload returns 503.
There are at most four HTTP handlers and a socket backlog of four.

The primary provider is OpenAI `gpt-transcribe`, using exactly
`POST https://api.openai.com/v1/audio/transcriptions` with `model`,
`file` (`voice.wav`), `languages[]=uk`, `response_format=json`, and
`stream=false`. It does not send the old singular `language` parameter,
keywords, or a prompt. The HTTP worker honors inherited proxy settings and
validates TLS. Redirects and retries are disabled. An absolute 20-second
subprocess deadline covers DNS, connect, upload, inference and response; the
worker is killed and reaped at expiry.

Network errors, timeouts, authentication/quota errors, rate limits, server
errors, other failed HTTP responses, and malformed responses fall back to
Nemotron over the existing private `/run/kira-asr/asr.sock`. A valid empty
OpenAI transcript is returned without fallback. The fallback uses explicit
`uk-UA` and a 190-second socket deadline; the existing native wrapper retains
its own inference cancellation policy. This broker never restarts or edits
the existing Nemotron service.

Successful JSON contains `text`, `provider`, `engine`, and a fixed
`fallback_reason` code (null for primary success). `/ready` and `/health`
report only local configuration, local budget availability, fallback health,
and aggregate process-local provider counts. When the primary key and local
budget are available, readiness returns immediately without probing fallback
and reports `fallback_ready=null`. `primary_configured=true`
indicates that a key is present, not that its account balance or permissions
have been validated. With no key or an exhausted local budget, readiness
depends on a Nemotron health probe. Health requests never call the paid API.

The default local cap is **600 audio minutes per calendar month in
Europe/Kyiv**. `OAK_STT_MONTHLY_MINUTES` may lower it, including zero, but
cannot raise it above 600. The private atomic ledger in the systemd state
directory (`/var/lib/oak-stt-openai/budget.json`, 0600) contains only month,
reserved audio milliseconds, and number of paid attempts. A reservation is
persisted and fsynced before a paid request. Failed and uncertain attempts
remain counted conservatively; no automatic refunds or retries occur.
Corrupt, unsafe, or unavailable ledger state disables the paid path and
uses fallback. This cap does not change the OpenAI account's budgets and
does not include API usage from other applications. At the verified rate of
$0.0045/min, 600 audio minutes correspond to approximately $2.70 before taxes.

The key is supplied only to the trusted `ubuntu` broker/worker through the
protected `/home/ubuntu/oak-stt-openai/secrets/openai.env`, variable
`OPENAI_API_KEY`. It is not passed to Oak's gateway/model/executor. The
environment file is optional so that the local fallback can operate before
a key is configured. A key change requires restarting only `oak-stt.service`.
Do not put credentials in tracked files, command arguments, or logs.

Audio, transcripts, and provider error bodies are never written to disk or
logs by this broker. Native fallback logging policy belongs to its existing
wrapper. Provider requests naturally send the accepted audio to OpenAI;
only explicitly approved live voice input should enter this route.

Checking out these templates does not install or start a service. For an
explicitly configured and authorized deployment:

```sh
sudo install -m 0644 /home/ubuntu/oak-stt-openai/service/oak-stt.socket /etc/systemd/system/oak-stt.socket
sudo install -m 0644 /home/ubuntu/oak-stt-openai/service/oak-stt.service /etc/systemd/system/oak-stt.service
sudo systemctl daemon-reload
sudo systemctl enable --now oak-stt.socket
```

For an owner who already created an API key, the included `import-key.py`
accepts it through hidden input in their own interactive SSH terminal:

```sh
python3 /home/ubuntu/oak-stt-openai/import-key.py
```

The helper refuses redirected input, checks private directory/file ownership,
rejects symlinks, preserves unrelated environment settings and atomically writes
only the configured private environment file with mode 0600. It never prints
the key. Do not paste a key into a chat, command argument or tracked file.
After import, restart only `oak-stt.service`; provider access still requires
an explicitly approved test. The helper source belongs in review; private
environment files and generated credential-transfer artifacts do not.

Socket activation starts the broker on the first connection. The unit runs
as `ubuntu` with Nice 10, CPUQuota 10%, MemoryMax 250 MiB, a private temporary
directory, and protected home/system mounts. Only its own systemd state
directory is writable. Verify the socket owner/mode and an idle `/ready`
response before enabling Oak's voice download path. The consumer timeout
should allow the primary plus fallback; 240 seconds is suitable.

Stop without affecting the existing Kira/Nemotron service:

```sh
sudo systemctl disable --now oak-stt.socket
sudo systemctl stop oak-stt.service
```

Offline validation (all network/provider/subprocess operations mocked):

```sh
cd /home/ubuntu/oak-stt-openai/service
python3 -m unittest -v test_server
systemd-analyze verify oak-stt.socket oak-stt.service
```

Official schema references, verified 2026-10-29:

- https://developers.openai.com/api/docs/guides/speech-to-text
- https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create
