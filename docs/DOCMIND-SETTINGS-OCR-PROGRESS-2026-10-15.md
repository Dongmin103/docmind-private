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

## Previously approved document inspector UI

The October 8 chat and commits `7ae6095` / `9123dcf` confirm removal of
Artifact, Full text/Ellipse and availability filter controls, Korean title
`문서 내용` and description `검색과 AI 답변에 사용되는 문서 내용입니다.`.
The user reaffirmed that exact scope on October 15: retain detail Search,
original preview and full content. The extracted inspector had missed these
changes; they are restored without changing main search or preview behavior.
Focused inspector/DocMind/OCR UI verification: 35 passed; focused lint and
`git diff --check` passed. No remaining text-mode component references.

## Rollout and browser verification

- Deployed committed runtime `9b1711f4e0dacd8352da98c034afe3684d37a196`
  (includes settings/OCR commit `ac56b26`) from a clean archive build.
  Image: `docmind-ragflow:diet-9b1711f4-1dac837c`.
  ID: `sha256:ad64097258deae460afcbdf16b4bfcd1a56eb6b155fa0f280daf2949fbd1e621`.
- Vercel deployment: `dpl_6mH5inuLwCwAMT8X5Y7t3EvW3M2d` at
  https://temporary-speedy-tuba-bfs78eb.vercel.app/docmind.
  Local and public HTML hashes match the frontend extracted from that image.
  Existing API rewrite retained.
- Waited for the ongoing user OCR job and cleanup to finish. Before/after rollout:
  228 indexed documents, 12,489 chunks, identical lifecycle state counts, zero
  pending cleanup. This release did not request production OCR or reindexing.
- API health (DB, document engine, Redis, storage), auth keys/settings hashes,
  configured environment/resource limits and source hashes passed. All three
  Windows worker/watcher tasks resumed. Admin server remains absent.
- The first verification saw a transient HTTP 401 after shared-session creation;
  a fresh endpoint check and the complete verification rerun both passed without
  code/config changes. Exact cause was not established. Actual public UI access
  also passed.
- Isolated synthetic browser check against exact image assets: OCR request stays
  visible as waiting, then parsing, then completion. Completion survives reload;
  acknowledging hides it across reload. Original HWP preview, detail Search,
  Korean document title and absence of retired toolbar controls verified.
  Direct `/user-setting/model` redirects to search.
- Public browser: five pre-existing OCR completions remain with acknowledgement
  buttons; no real acknowledgement was clicked. The user's PDF detail shows
  Korean title/description, Search and original-preview button, without
  Artifact/Full text/Ellipse/filter controls. Settings navigation is absent.
- Additional inspector/import/query tests: 11 passed. Removed obsolete test-only
  textMode props after the runtime build; card tests rerun: 3 passed. No runtime
  change follows the deployed commit. Independent review found no actionable
  issue in the final Search-preserving commit.

Deployment receipts, rollback configurations (private), and browser screenshots
are in `.local/docmind-settings-ocr-deploy-20261015/` outside Git. Full-suite
baseline limitations above remain; real OCR inference was not rerun for this UI
release.
