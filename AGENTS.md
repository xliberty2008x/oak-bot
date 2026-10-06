# Working on Oak

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
