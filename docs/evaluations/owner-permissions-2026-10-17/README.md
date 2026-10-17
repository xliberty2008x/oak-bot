# Owner-authorized execution and MCP confirmation repair

The owner reported that attaching an ElevenLabs reference file and creating a
GitHub issue repeatedly returned `user rejected MCP tool call` after explicit
consent. The deployed adapter rejected the native confirmation before showing
any owner interface.

Codex 0.162.1 represents app tool approval as a standard MCP form with empty
`properties` and `_meta.codex_approval_kind = "mcp_tool_call"`. Oak required
one to three fields and `additionalProperties: false`, although the native
schema does not emit that extension. This rejected valid confirmations and
ordinary supported enum forms. See the exact
[runtime source](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/core/src/mcp_tool_call.rs).

The adapter now routes those confirmations to Oak's existing owner buttons and
resumes the same native call once. It retains only routing and a bounded title,
not arguments or authentication metadata. Unsupported elicitation formats emit
a fixed explanation that distinguishes adapter refusal from owner refusal.

For this owner's authorized deployment, connected-app approvals use native
`apps._default.default_tools_approval_mode = "approve"`. Full inner runtime
access requires Oak's existing attested OS execution profile; it removes the
redundant inner command restrictions while retaining the selected OS identity,
mounts and host policy. Every thread and turn receives the chosen policy.
`on-request` remains available for genuine downstream human input. Portable
defaults and other deployments are unchanged.

## Checks

- Runtime/events/executor/integration: 74 tests passed.
- Input requests and Telegram transport: 54 tests passed. These include actual
  native confirmation shapes, owner binding, once-only response, stale requests,
  explicit declines, non-secret schema compatibility and secret canaries.
- Real installed app-server plus a local synthetic MCP server reproduces the
  exact rejection string on decline and successful tool completion on accept.
  Direct `mcpServer/tool/call` bypasses model-facing approval and is not proof of
  the connected-app approval setting.
- [Real model smoke](model-smoke.json): the existing subscription-backed
  `gpt-6.1-sol`, running as executor UID 1900, completed a native command,
  temporary workspace file write/read/delete and public HTTPS GET with no
  approval requests. The test used an ephemeral thread and sent no Telegram
  message or third-party write.
- [Actual model MCP comparison](model-mcp-smoke.json): `auto` emitted the empty
  confirmation and declining reproduced the reported error with zero fixture
  executions; `approve` emitted no confirmation and executed the same harmless
  local MCP fixture exactly once. Both used the installed native runtime and
  real model. Genuine server-origin elicitations still reach the client.

These checks do not claim that a paid ElevenLabs generation or a live GitHub
issue creation was performed. Existing ambiguous external actions are not
automatically retried.
