# A2UI protocol pin

Unmodified files from a2ui-project/a2ui commit
`db4306536438df46e4f0443b9c4ec0d5f1a42dc4`:

- `specification/v0_9_1/json/server_to_client.json`
- `specification/v0_9_1/json/client_to_server.json`
- `specification/v0_9_1/json/common_types.json`
- Apache 2.0 `LICENSE`

Source: https://github.com/a2ui-project/a2ui/tree/db4306536438df46e4f0443b9c4ec0d5f1a42dc4/specification/v0_9_1/json

Their schema IDs still contain `v0_9`; the message version enumeration accepts
`v0.9.1`. Oak accepts only `v0.9.1`. The official server schema resolves
`catalog.json` to the custom Oak catalogue during offline validation. Oak does
not claim support for the full Basic Catalogue or another renderer's extensions.
