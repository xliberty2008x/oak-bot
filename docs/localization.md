# Incremental product text catalogue

`oak/strings.py` owns shared product strings. Ukrainian (`uk`) is the default.
The optional top-level configuration key `locale` accepts language tags such as
`uk-UA`; an unknown or unavailable language falls back to Ukrainian. Only
Ukrainian is currently provided. Missing keys show the fixed message
`Текст недоступний`, without exposing an arbitrary key or runtime payload.

Call `text(key, locale, **values)` for server product text. The authenticated
Mini App locale endpoint serves `catalogue(locale)` and its browser formatter
is `createLocale(catalog).t(key, values)` from `oak/web/locale.js`. Keep this
catalogue the single source for strings shared across the two clients. This is
an incremental extraction: existing strings outside the catalogue remain
Ukrainian until their feature is updated. It does not claim a fully translated UI.

Substitutions use simple named placeholders, for example `{count}`. Values may
be strings, safe integers, booleans or null; format dates, fractional numbers
and units before substitution. Missing values retain their placeholder so a
broken label is visible during review. Templates never evaluate expressions,
parse HTML or recursively substitute user content. Assign browser text with
`textContent` and preserve the existing safe renderer for Markdown surfaces.

Recovery type labels and the artifact fallback name use the catalogue. Shared
task/status, memory, activity and file controls have keys for incremental client
adoption. Add a Python fallback test and browser formatter parity case when
changing formatting behavior; use the existing `unittest` and Node fixtures.
