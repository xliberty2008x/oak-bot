# Harness² method dependency

Unmodified Python method core and deployment-value module from
[google-research/harness2](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335),
commit `1cfe254e8d25b15f37048c3730134686abe60335`, package version 0.1.0,
Apache-2.0. `UPSTREAM.json` records every included source file's SHA-256;
Oak verifies both that manifest and the files before importing the method.

No source patches are applied. The upstream benchmark launchers, benchmark
datasets, API bridges, Codex distribution and external-provider setup are omitted.
Oak supplies the benchmark/native executor and an explicit ChatGPT subscription
role adapter. Propose–Probe–Compose, validation, adoption, reflection, library and
method-level resume come directly from this dependency. Oak's independent outer
acceptance/activation is separate from upstream's internal adoption.

The upstream prompt factory recognizes only the `codex` and `opencode`
surfaces. Oak uses the Codex instruction schema, restricted to `AGENTS.md`,
and injects its accepted text as a scoped Oak procedure. Extra scripts, skills,
plugins, hooks and delegated agents are rejected in this first adapter; the
upstream Codex launcher is not used. Its API endpoint authentication and the
independent OpenCode/Claude improvement channels are replaced explicitly by
`SubscriptionRole`, using Oak's existing RuntimeClient and ChatGPT login for
INV, DOM, composition, selection when applicable, and reflection. Model and
effort are fixed at `gpt-6.1-sol` / `low`; no API-billing fallback is provided.

Python 3.12 or 3.13 is required by this pinned upstream release. Its core needs
only the standard library. Replacing these files requires a deliberate new pin,
manifest and integration validation; importing another installed `harness2`
package is rejected.
