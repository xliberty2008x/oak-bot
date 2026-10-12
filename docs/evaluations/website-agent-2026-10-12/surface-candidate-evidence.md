# Surface candidate descriptions — source correction, 2026-10-12

The preserved V5 diagnostic selected a broader surface containing an unrelated
field and extra action, despite relevant action-only regions being present.
The later component response was invalid; the native agent continued the
workflow. This is evidence of an incorrect selection, not proof that reference
joins caused it. The final fixture expectation also included account-preference
editing outside its supplied sign-in goal. V5 remains failed and is not relabeled
as independent acceptance evidence.

Previously every surface option repeated the same generic description, leaving
the model to join membership references to flat controls, actions and text.
The source correction puts each candidate's observed field labels/types/roles,
native ownership, action labels/capabilities, and associated text directly in
that option's structured description. It uses the existing sanitized model
projection. Native option values/captions, URLs and private values remain absent.
Global headings/help/errors, the supplied goal, after-input state and accepted
human-step history remain available. The redundant flat control/action/surface
tables are omitted only from the surface-selection request.

Candidates are deduplicated only when their kind and exact sets of control,
action and text references match. The first existing reference represents those
equivalent observations; explicit structural callers may still name an
equivalent observed alias. Different kinds, different text evidence, broader
scopes and equal-looking controls with different references remain distinct.
No smallest-scope rule, site classifier or confidence threshold is introduced.
The compiler still checks all selected-surface members and native ownership.

The local maximum serialized request is 4 MiB, measured with the same default JSON
escaping as the transport before reserving any model call. Oversized or
unserializable requests fail without a reservation, request, truncation or retry.
This generous bound accommodates the existing 64 KiB scene and up to 16
candidate descriptions, including escaped Unicode. This application bound does
not establish a provider context limit. Existing model/provider pins, maximum
three dependent phases and the 12-call lease budget remain unchanged;
equivalent aliases can remove a redundant surface-choice phase.

## Primary evidence

The installed official TypeSafe skill and live documentation were read before
the change:

- [Choice](https://docs.typesafe.ai/primitives/choice) recommends descriptions
  that distinguish options, including structured descriptions when options are
  repeatedly confused. Its descriptions can contain application-defined fields.
- [API reference](https://docs.typesafe.ai/api) explicitly permits object-valued
  Choice criteria; the selected option and probability map retain the existing
  closed-set response contract.
- [State](https://docs.typesafe.ai/concepts/state) recommends named structured
  context and keeping related facts together when a judgment compares them.
- [Function-calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling)
  separates semantic option descriptions from unchanged typed execution rules.

These sources support the payload structure. They do not establish that this
change improves real Jev accuracy. Focused tests cover exact option evidence,
alias equivalence and non-equivalence, native constraints, privacy projection,
quota behavior, JSON-byte boundary enforcement and a maximum-count Unicode
scene. No new model inference or deployment accompanies this source correction.
