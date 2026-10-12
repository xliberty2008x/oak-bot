# Official A2UI runtime and Oak compatibility adapter

Source adapter: `frontend/a2ui-runtime.js`; exact dependency lock:
`frontend/package-lock.json`. `runtime.js` is a locally served ES module built
with esbuild 0.25.12. No CDN, bare npm imports, Node installation or npm command
is needed at runtime. `packages.json` records every package included in the
bundle, archive integrity and its SHA-256. `THIRD-PARTY-LICENSES.txt` contains the
corresponding full licences; the bundle also preserves upstream legal comments.

The direct dependencies are the published `@a2ui/lit@0.13.0` and
`@a2ui/web_core@0.13.0`, both Apache-2.0. The import path is `v0_9`, while Oak's
wire version remains `v0.9.1` and catalog remains `urn:oak:a2ui:canonical:v1`.
Those are separate version identifiers. Exact transitive pins and licences are
in the lock and generated provenance; esbuild is a build dependency only.

Reproduce with Node supporting the esbuild target (tested Node 24.20.0):

```sh
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
npm --prefix frontend run check
```

The build check compares the bundle, package provenance and all licence texts
byte-for-byte. No install script is required on the tested Linux ARM64 machine:
esbuild uses its locked optional platform binary. Git tracks reviewed artifacts;
`node_modules` is excluded.

The official `MessageProcessor`, `DataModel`, GenericBinder, component models,
Lit surface and keyed rendering own the ordinary UI. Oak retains its validated
catalog, action transport, server revisions/receipts, expiry and conversation
binding. Card, Column and Row use the upstream implementations. TextField subclasses
the upstream element only to add native `required`, 2000-character bound, an
associated label and existing native styling. Small compatibility entries retain
literal Text, a native single-select ChoicePicker and Oak's label-based
primary/quiet/danger Button. Adapter `oak-a2ui-lit@1.1.0` adds Divider, Badge,
Metric, Progress, Table, Chart and Image without exposing other Basic Catalogue
components. They use upstream bound props/setters/actions;
there is no replacement tree, JSON-pointer writer or action-context resolver.

The canonical JSON schemas remain the accepted property contract. The adapter
adds binder annotations for narrow path-only bindings and action events. A
`never` union branch changes no accepted values. The whole Basic Catalog,
functions, markdown HTML, URLs, styles and unregistered components are not made
available. Upstream implicit accessibility/metadata envelope extensions are
rejected by the exact Oak property gate before processing.

Tables use native semantic markup; charts and progress use labelled SVG with
literal text and numeric geometry. Negative chart values retain their zero axis.
Image elements accept only conversation artifact IDs and use the host's
authenticated, abortable, size-bounded loader. Active elements reference-count
blob URLs; last-owner removal aborts its request and revokes its URL. Disposal
also rejects late async results. No model URL or bearer token becomes an image
source, and removed images are not retained in a renderer cache.

The private website iframe does not import this runtime. Its human values stay
on the broker's separate channel and trusted widgets, outside ordinary A2UI
models/action context/sendDataModel, AG-UI and model calls. This migration does
not replace browser collection, Jev planning, lease security or provider proof.

Acceptance and retained failures: `docs/evaluations/a2ui-lit-2026-10-12/README.md`.
