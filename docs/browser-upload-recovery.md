# Browser upload failures and recovery

Oak must distinguish selecting a file from the website accepting, processing or
publishing it. A successful browser tool call proves only the operation described
by its receipt. Keep the requested task active through verification, and explain
any remaining blocker using observed evidence.

## Local upload preflight

The managed browser accepts one to eight regular workspace files with an
aggregate limit of **128 MiB (134,217,728 bytes)**. It snapshots their bytes before
any browser operation. Private service paths, symlinks, hardlinks, missing files
and sources that change during the read are rejected. The original is preserved.

These failures return ordinary structured tool results through the execution
worker, so the assistant receives a useful cause instead of only `ValueError`:

| Error | Meaning | Next step |
| --- | --- | --- |
| `payload_too_large` | The known payload bytes exceed the local upload limit. `observed_bytes` and `max_bytes` give the measured constraint. | Inspect an existing valid prepared copy, or prepare a separate smaller copy within the authorized quality/content choices. If exact original bytes are required, identify an already authorized supported route with a sufficient limit. |
| `upload_source_invalid` | The source cannot safely be snapshotted. A curated message identifies expected workspace, file or stability constraints when available. | Inspect the source and prepare a stable regular workspace copy. Preserve the original and respect private-path restrictions. |

Both have `error.stage=upload_preflight` and `outcome=not_attempted`: this attempt
has not sent the payload to the website. For example, a synthetic file one byte
over the limit returns:

```json
{
  "ok": false,
  "action": "upload",
  "outcome": "not_attempted",
  "error": {
    "code": "payload_too_large",
    "stage": "upload_preflight",
    "observed_bytes": 134217729,
    "max_bytes": 134217728
  }
}
```

The actual error also includes a safe message and recovery advice. It contains
no source path, account identifier, entered value or arbitrary exception text.
The measured bytes can be a known subtotal when an earlier file already exceeds
the aggregate budget; they need not enumerate later, unread files.

Do not diagnose a provider rejection, login failure or account quota from a local
preflight error. Do not retry the same oversized bytes under another filename or
through another selector. Reducing file size changes a derivative: verify its
actual content and quality against the request before using it.

## Verify each stage separately

| Evidence | What it establishes |
| --- | --- |
| Upload receipt with `verification=browser_file_selection` | Chromium selected the workspace bytes. `server_upload_verified=false` explicitly leaves website acceptance unverified. |
| Current website response naming an accepted upload | The site reports acceptance of that upload. |
| Current processing status | Processing is pending, completed or failed as observed. Acceptance alone does not prove processing finished. |
| Saved draft or matching upload/video identifier | The site retained an identifiable draft/upload. A draft is not publication. |
| Observed final visibility/publication state for that item | The requested publication state is supported by site evidence. Public playback/reachability requires its own check when relevant to the task. |

Inspect current controls, frames and tabs. Hidden file inputs can be used
directly with `oak_browser(upload)`; an observed chooser is also supported.
Use visual interaction when configured and useful. These routes share the
managed desktop browser, but authentication in a separate broker-owned website
session does not automatically transfer to it. `oak_account_browser` does not
currently expose an upload action. Discover an available authorized capability
before promising a fallback; never copy cookies or ask for credentials in chat.

After dispatch, a timeout or failed observation may leave `outcome=uncertain`.
Read the current website state before another mutation. Check for an existing
draft/upload and avoid duplicate external effects. The local preflight guarantee
must not be applied to an action that may already have reached the site.

## Retain ownership when human input is necessary

Resolve routine file preparation, observed control selection and recoverable
workflow steps yourself. Ask the owner only for an indispensable decision,
permission or private verification step that Oak cannot perform with its
available authorized capabilities.

A useful escalation states the attempted operation, concrete evidence, what is
complete and incomplete, the smallest required owner action, and what Oak will
do afterward. For an unknown cause, say what is unknown and which observation
is missing. Avoid handing the whole upload task back with “upload it yourself.”

Use a provisioned private waiting channel for credentials/verification; ordinary
forms and chat cannot collect passwords, OTPs, tokens or cookies. Private website
waiting can resume the same native request. Manual desktop takeover stops active
turns; after ending it, the owner must send a continuation. Re-observe preserved
state when continuing. Never promise automatic resumption on an unavailable
route or treat silence as authorization to publish.

The regression tests cover local validation, immutable bytes, safe diagnostics,
worker/native-tool propagation and uncertain dispatch. They do not establish
live YouTube compatibility or native assistant recovery quality; those need
separate operational evaluations with fresh examples.
