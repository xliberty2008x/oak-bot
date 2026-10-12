# Working on Oak

- Before changing UI, forms, renderers, catalogues or frontend dependencies, read [DESIGN.md](DESIGN.md). Reuse existing components, preserve private-channel boundaries, and update the design contract in the same PR when changing the UI stack.
- Before changing Jev/TypeSafe integration, use the official [typesafe-ai skill](https://github.com/typesafe-ai/skills/tree/main/skills/typesafe-ai). Read its current documentation index, API/SDK contract and closest cookbook before designing new abstractions. Preserve enough relevant evidence for the decision and evaluate real model behavior separately from mocked protocol tests; do not invent endpoints, response fields or quality thresholds.
- For a fresh VM or a complete deployment, read [.agents/skills/oak-bootstrap/SKILL.md](.agents/skills/oak-bootstrap/SKILL.md) and use `scripts/bootstrap-vm.sh`. The skill covers the machine, desktop and Telegram/Mini App gateway, including readiness checks.
- Keep Oak's core reusable. Deployment personas, memories, tokens and account state stay outside Git.
- Use the subscription-backed runtime with ChatGPT authentication. Never add API-key billing fallback or silently change `gpt-6.1-sol`.
- Use Oak names in product text. Name technical dependencies accurately in setup and implementation documentation.
- Keep fixes small and use focused standard-library tests. Do not create a separate testing framework.
- Preserve durable input, schedule, approval and outgoing-delivery state. Do not blindly replay uncertain external actions.
- Keep memory scoped to the selected conversation; import only explicitly supplied exports.
- Check bot identity and webhook ownership before starting a Telegram poller. Never copy another browser's authentication state.
- Never log credentials, token-bearing URLs, private prompts, memories or account data.
- Distinguish unit checks, real local tool checks, model execution and live Telegram verification. Do not claim untested provider access.
- Treat public examples as portable: no private persona text, user IDs, bot credentials or host-specific model paths.
