# Settings visibility and OCR progress — 2026-10-15

User decisions: restore the October 8 hidden settings UI and retain explicitly
requested OCR documents through processing and completion. A successful result
leaves the dashboard only after the user acknowledges it. Failures remain visible.

## Changes

- Removed the DocMind settings menu/render/lazy component. `/user-setting/*` and
  `/profile-setting/*` redirect to DocMind; `?view=settings` shows search.
  Server model configuration and APIs are unchanged.
- The authorized source projection exposes sanitized OCR job status and history
  by source version. Waiting, preparation, OCR, indexing, cleanup, retry and error
  states remain visible in an independent dashboard section, regardless of filters.
- Completion requires successful cleanup, certified version and matching parser/
  chunk identities. The current version also requires a usable active index.
  Historical OCR results survive a newer ordinary source version and are labeled.
- Acknowledgement stores only file/version identifiers in this browser's local
  storage. It merges prior tab acknowledgements. Storage failure is reported;
  a different browser has its own acknowledgement list. Deleted/inaccessible
  source documents are not retained outside the authorized hierarchy.
- Existing explicit OCR consent and 10-second hierarchy polling are retained.
  No automatic OCR execution or changes to parsing/embedding settings.

## Verification before rollout

- Settings RED: 16 failures; GREEN: 77 passed.
- OCR UI RED: three failures; final focused UI/import-boundary suite: 227 passed.
- Source projection RED: missing OCR fields; final SQLite projection suite:
  31 passed, including historical completion certification and new-source retention.
- Full frontend: 429 passed, 13 failed across five suites. All 13 failures reproduce
  on unchanged `effaaf4`: `utils/__tests__/chat.test.ts`, `utils/tests/chat.test.ts`,
  `components/__tests__/switch-fom-field.test.tsx`,
  `hooks/__tests__/logic-hooks-use-scroll-to-bottom.test.tsx`, and
  `components/highlight-markdown/__tests__/index.test.tsx`. Full suite is not green.
- TypeScript: 29 existing diagnostics, no new diagnostics. Focused lint and
  retired-runtime verification passed. Existing i18n and Peewee warnings remain.
- Independent review findings fixed: stale deleted-component test entry, stale
  tab acknowledgement overwrite, and uncertified historical completion. Final
  read-only reviewer reported no remaining actionable findings.

Local logs: `.local/docmind-feature-integration/*followup*`, `*ocr-progress*`,
`*ocr-projection*`, and `settings-hidden-*`. Production rollout is recorded below
after artifact and live verification.
