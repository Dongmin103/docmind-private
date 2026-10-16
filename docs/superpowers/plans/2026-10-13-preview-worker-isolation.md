# DocMind Preview Worker Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. User-selected implementer: **gpt-6.1-sol, reasoning effort high**. 기본 실행은 해당 모델의 한 구현 세션에서 순차 진행한다. 이 문서 작성은 모델 전환·구현 시작·별도 채팅 생성을 뜻하지 않는다.

**Goal:** 색인 서버의 응답 대기와 독립적으로 미리보기를 시작하되, 복호화 단일 실행과 메모리·임시 평문 경계를 유지한다.

**Architecture:** Windows 색인 작업자와 미리보기 작업자를 역할별 1개로 분리한다. 전역 복호화 잠금은 실행/검증에만 사용하고 서버 전달 응답은 역할별로 기다린다. 기존 Docker 본체·미리보기 처리기를 재사용한다.

**Tech Stack:** Windows PowerShell 5.1, .NET 프로세스/mutex, Python 3.13/Quart/Peewee, React/TypeScript, Docker on WSL2.

**Spec:** `docs/superpowers/specs/2026-10-13-preview-worker-isolation-design.md`

## Global Constraints

- 구현 저장소는 `C:/DocMindDev/docmind`. 먼저 루트·app/AGENTS.md, PRD, 최신 배포 문서를 읽고 필요 시 프런트 CLAUDE.md도 읽는다. 사용자 변경을 덮어쓰지 않는다.
- 현재 확인한 별도 변경: `app/docker/docker-compose-windows-dev-surya-gpu.yml`과 두 미추적 진단 문서. 이 계획의 커밋에 섞지 않는다. 실행 시작 시 다시 확인한다.
- 실제 복호화 동시 실행 1개, 색인 작업자 1개, 미리보기 작업자 1개, 미리보기 변환 동시 실행 1개.
- 입력 64MiB, 파생물 16MiB, 예약량 96MiB, 세션 2개, tmpfs 128MiB, 처리기 256MiB를 유지한다.
- 원문 영구 캐시·복제 모델·전체 재색인·source 확대·Windows 전체 재부팅은 이 구현 범위가 아니다.
- 실제 설정·개인키·원문·세션 ID·운영 데이터는 Git에 넣지 않는다. 측정 결과 원본은 `.local/preview-worker-isolation/`에 둔다.
- 미리보기 idle 1초, 연결 실패 backoff 5초, 브라우저 polling 1초, heartbeat 30초. 미리보기 잠금 대기 상한 30초/lease 안전 여유 10초.
- 새 메모리 제한값으로 문제를 숨기지 않는다. 기준선 또는 겹침 시험이 설계의 배포 판정선을 넘으면 미배포 상태로 원인과 필요한 조정을 보고한다.
- 구현 코드와 검증을 먼저 완성한다. 사용자에게 구현·배포 실행 지시를 받기 전에는 이 계획 작성만으로 실행하지 않는다.

## Review Focus

1. 부모 종료/abandoned mutex 후 살아 있는 복호화 프로세스와 새 작업이 겹치지 않는가 — Task 2.
2. 취소·만료·버전 변경이 잠금 대기 중 발생해도 실행 직전 차단되는가 — Tasks 2–3.
3. 역할별 정리와 이전 worker ID의 영수증 복구가 서로 충돌하지 않는가 — Tasks 1, 5.
4. 두 탭/HWP/최대 크기 문서에서 기존 공간 제한과 메모리 예산을 지키는가 — Tasks 4, 6.
5. 배포 일부 실패·원복·재부팅 때 혼합 작업자 또는 중복 작업자가 생기지 않는가 — Task 5.

## Task 0: 기준선과 실행 환경 고정

**Files:** Read `docs/LOCAL-GTE-DEPLOYMENT-2026-10-12.md`, 실제 예약 작업 런처, 관련 `.local` release metadata. Create ignored `.local/preview-worker-isolation/baseline.json`.

- [ ] Git 상태와 현재 배포 이미지 ID·작업 스케줄러 설정·소스/배포 사본 해시를 기록한다. 구현 격리가 필요하면 using-git-worktrees 절차로 기존 적절한 worktree를 확인하고 사용한다.
- [ ] Windows 작업자 private/working set, Windows 가용 RAM/commit, WSL available/swap/PSI, 관련 cgroup memory.current/events와 컨테이너 제한을 5분 측정한다. 시각은 Asia/Seoul, 원자료 timestamp는 UTC도 허용한다.
- [ ] 실제 모델 바인딩과 BGE 요청 경로를 확인해 사용/미사용/불명으로 기록한다. BGE를 중지하거나 모델을 변경하지 않는다.
- [ ] 기준선이 메모리 판정선을 넘으면 기능 구현·합성 테스트는 격리 환경에서 진행하되 라이브 배포는 보류 대상으로 표시한다.

## Task 1: 역할과 정리 소유권 분리

**Files:** Modify `tools/windows/Start-DocMindUEncryptorHostWorker.ps1`, `tools/windows/DocMindUEncryptorHostWorker.Common.ps1`, `tools/windows/docmind-host-worker.config.example.json`, `tools/windows/Test-DocMindWorkerThroughput.ps1`, `tools/windows/Test-DocMindUEncryptorHostWorker.ps1`. Create `tools/windows/Test-DocMindPreviewWorkerIsolation.ps1`.

**Interfaces:** `Start-DocMindUEncryptorHostWorker.ps1 -Purpose ingest|preview` (default ingest); new common `Get-DocMindWorkerRolePaths(Config, Purpose)` returns WorkRoot, ReceiptRoot, ControlRoot. `ControlRoot` is shared metadata-only and separate from both plaintext roots. Runtime configs use distinct worker IDs and disjoint work/receipt roots.

- [ ] 먼저 실패하는 테스트를 작성한다: 색인 모드는 preview claim/replay/reaper를 호출하지 않음, 미리보기 모드는 ingest 경로를 호출하지 않음, 같은 역할의 중복 작업자는 시작 거부, `Once`·대상 색인·source 필터의 기존 동작 보존. 테스트 이름과 코드 주석은 기존 저장소의 영어 형식을 따른다.
- [ ] Windows PowerShell 5.1에서 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindPreviewWorkerIsolation.ps1`을 실행해 새 계약이 아직 구현되지 않아 실패하는 것을 확인한다.
- [ ] 위 인터페이스와 역할별 singleton을 구현한다. 모든 저장소 `PreviewOnly` 호출부를 검색해 새 Purpose로 변경하고 혼합 claim/fairness 분기를 제거한다. 기존 source 승인 범위를 보존한다.
- [ ] 역할별 정리 root뿐 아니라 시작 시 정리·주기적 영수증 재전송도 분리한다. preview_disabled는 preview 모드 시작을 거부한다.
- [ ] `Test-DocMindPreviewWorkerIsolation.ps1`, `Test-DocMindWorkerThroughput.ps1`, `Test-DocMindUEncryptorHostWorker.ps1`을 Windows PowerShell 5.1에서 실행해 PASS를 확인한다. 현재 테스트는 AST 추출을 사용하므로 추출 대상과 변수도 실제 코드 계약에 맞춘다.
- [ ] 관련 파일만 커밋: `refactor: separate ingestion and preview worker roles`.

## Task 2: 복호화 잠금 구간과 종료 실패 처리

**Files:** Modify 위 worker/common/test 파일. Create `tools/windows/DocMindDecryptGate.ps1`, `tools/windows/Test-DocMindDecryptGate.ps1`.

**Interfaces:** `Enter-DocMindDecryptGate(ControlRoot, DeadlineUtc, ValidateLease)` returns a gate handle; `Exit-DocMindDecryptGate(Gate, ProcessExitConfirmed)` releases exactly once only when safe. `Invoke-DocMindDecryptProcess(Gate, Executable, SourceFile, OutputFile, TimeoutSeconds)` owns lifecycle records and termination checks; it returns after confirmed exit or raises an error and persists a blocked state. Keep the global mutex name `Global\DocMind-uEncryptor2-SingleRun`.

- [ ] 두 실제 합성 Windows 프로세스로 red test: 첫 작업의 전송 응답을 막은 상태에서 두 번째 복호화가 실행된다. 반면 두 복호화 프로세스의 실행 구간은 겹치지 않는다. 실제 uEncryptor·사용자 원문은 단위시험에 사용하지 않는다.
- [ ] 추가 red cases: timeout, 종료 실패, 부모 강제 종료, abandoned mutex, PID 재사용, 시작 도중 불완전한 owner record, 이중 release, 늦은 전송 ACK. 종료 실패가 `COMPLETE`로 보고되지 않는 것을 검사한다.
- [ ] helper와 제한된 재대기를 구현한다. owner record는 PID와 시작시각/실행 파일 식별을 함께 사용한다. 종료를 증명하지 못하면 지속 차단한다. 부모 사망 이후의 자식 프로세스까지 종료/생존 판정할 수 있는 관리 방식으로 구현하고 합성 자식 프로세스 시험으로 입증한다.
- [ ] 출력·원본 무결성 검증 뒤, HTTP 전송 전에 gate를 반환한다. payload 전송과 ACK는 기존 동작을 유지하되 응답 body까지 timeout을 적용한다.
- [ ] `Test-DocMindDecryptGate.ps1` 및 Task 1의 세 회귀 스크립트를 실행해 PASS를 확인한다.
- [ ] 관련 파일만 커밋: `fix: release decrypt gate before artifact delivery`.

## Task 3: 잠금 대기 중 미리보기 유효성 확인

**Files:** Modify `app/api/apps/services/docmind_preview_service.py`, `app/api/apps/restful_apis/docmind_api.py`, `app/test/unit_test/api/apps/services/test_docmind_preview_service.py`, `app/test/unit_test/api/apps/test_docmind_preview_http.py`, Windows worker/common와 격리 테스트.

**Interfaces:** `validate_worker_lease(session_id: str, *, worker_id: str, version_id: str, fencing_token: int) -> dict`; POST `/cloud-sync/host-worker/previews/<preview_id>/validate`, existing signed worker envelope. Success `{valid: true, lease_expires_at: ISO8601}`; failure follows existing PreviewError signed response. No lease renewal or lifecycle change.

- [ ] red tests: wrong owner/fence/version, expired lease, CANCELLED/READY session, source replaced/deleted, user access revoked, unauthenticated request. Assert no DB writes and no plaintext output.
- [ ] `_leased`와 `_source_matches_session(check_access=True)`를 재사용해 조회 전용 검증을 구현한다. artifact/status에서도 현행 최종 검증을 유지한다.
- [ ] worker gate의 ValidateLease callback과 연결한다. 잠금 대기 중 최대 1초 간격, 획득 뒤 실행 직전 다시 확인한다. 검증 실패 때 exe 미실행, 정리/상태 ACK는 가능한 범위에서 기존 계약으로 마무리한다.
- [ ] 격리된 Linux 테스트 환경의 `app/`에서 `uv run pytest test/unit_test/api/apps/services/test_docmind_preview_service.py test/unit_test/api/apps/test_docmind_preview_http.py -q` 실행. Windows의 격리/gate 회귀 테스트도 PASS 확인.
- [ ] 관련 파일만 커밋: `feat: validate preview leases before decryption`.

## Task 4: 화면 상태·측정값과 bounded polling

**Files:** Modify `app/web/src/components/document-preview/temporary-original-preview.tsx`, `app/web/src/components/document-preview/__tests__/temporary-original-preview.test.tsx`, Windows worker의 `Save-HostTiming`. 필요한 경우 existing service tests의 오류 코드 허용 목록 갱신.

**Interfaces:** backend busy code `PREVIEW_DECRYPT_BUSY`; timing fields `lock_wait`, `decrypt`, `delivery_roundtrip`, `host_cleanup`, `status_ack`, `host_total`. Contents/paths/tokens are excluded. Preview timing must work for every already-authorized source, not require the existing dept-2-only benchmark option.

- [ ] red tests: polling 1초, heartbeat 30초 유지, cancel/unmount 후 polling·fetch 중단, blob URL 해제, 오래된 응답 무시, busy 오류가 명확한 재시도 문구로 표시됨. 두 탭 및 공간 제한의 기존 동작을 보존한다.
- [ ] 위 간격을 적용하고 DECRYPTING 문구를 '복호화 준비 중입니다.'로 바꾼다. Busy 문구는 '다른 문서의 복호화가 진행 중입니다. 잠시 후 다시 시도해 주세요.'로 한다. 실제 신호가 없는 세부 단계를 새로 만들어 표시하지 않는다.
- [ ] 단계별 시간 기록을 추가한다. 브라우저 첫 페이지 렌더 완료는 실제 렌더 시점을 측정하며 READY나 blob 응답 완료로 대신하지 않는다.
- [ ] `app/web`에서 `npx jest src/components/document-preview/__tests__/temporary-original-preview.test.tsx --runInBand --coverage=false`, `npm run type-check`, `npm run build`를 실행한다. 관련 기존 document-preview 테스트도 포함한다.
- [ ] 관련 파일만 커밋: `fix: report bounded preview waits and release resources`.

## Task 5: 배포·원복 도구와 소유권 이전

**Files:** Create `tools/windows/Set-DocMindPreviewWorkerIsolation.ps1`, `tools/windows/Test-DocMindPreviewWorkerDeployment.ps1`, `docs/PREVIEW-WORKER-ISOLATION-RUNBOOK.md`. Modify 필요한 예제 설정만. 실제 런처·설정·XML·이미지 목록은 ignored `.local/preview-worker-isolation/`에 생성.

**Interfaces:** deployment script `-Action Prepare|Apply|Rollback -ManifestPath <path>`; Prepare is read-only to live tasks and writes a manifest with before/after hashes and preserved enabled/running states. Apply validates quiescence and exact prepared artifacts. Rollback restores those captured artifacts and states; no DB schema/data rollback.

- [ ] red tests with mocked scheduler/filesystem: Apply fails before mutation if active decrypt/blocked gate/unresolved cleanup, partial registration failure restores prior state, reapplying does not duplicate tasks, Rollback never runs old mixed worker beside new workers.
- [ ] Prepare/Apply/Rollback을 구현한다. 실제 실행 중인 `.local` 사본과 예약 작업 인자를 확인한다. 오래된 10월 1일 Compose 파일을 그대로 재실행하지 말고 현재 이미지/환경/마운트를 보존한다.
- [ ] 이전 worker ID의 남은 영수증을 승인된 원래 상태 경로로 ACK 처리한 뒤 ID를 전환한다. 평문 root를 무작정 이동하거나 삭제하지 않는다. 새 worker별 root는 생성 전 경로·ACL을 검증한다.
- [ ] 정상 API readiness 후 작업자를 시작한다. 부팅 시에는 준비되지 않은 API에 대해 제한된 backoff를 적용한다. 기존에 비활성화된 source watcher를 새로 활성화하지 않는다.
- [ ] 격리 시험에서 Apply/재시작/Rollback을 검증하고 `Test-DocMindPreviewWorkerDeployment.ps1` PASS 확인.
- [ ] 관련 파일만 커밋: `feat: deploy isolated preview workers with rollback`.

## Task 6: 실제 겹침 시험과 배포 판정

**Files:** Create `tools/windows/Measure-DocMindPreviewIsolation.ps1`, `app/scripts/preview_worker_isolation_probe.py`; 결과 `.local/preview-worker-isolation/measurements/`. Runbook에 비민감 요약만 반영.

**Interfaces:** sampler `-DurationSeconds 900 -IntervalSeconds 1 -OutputDirectory <ignored-path>`; harness controls synthetic ingest delay 540초, PDF <=1MiB/<=5쪽 20회, 큰 파일/형식/cancel 시나리오와 시간을 기록한다. 문서 원문·키는 출력하지 않는다.

- [ ] sampler 단위 검증: 측정 누락을 0으로 처리하지 않음, OOM 카운터 증가 감지, 공유 tmpfs 중복 합산 방지, MiB/GiB 단위 일치, Windows/WSL 가용 메모리를 구분함. 컨테이너 재시작/카운터 리셋은 새 기준선으로 숨기지 않고 시험 실패로 기록한다.
- [ ] 기존 혼합 방식에서 합성 540초 지연이 미리보기를 막는 baseline을 기록하고, 분리 방식에서 미리보기가 색인 응답보다 먼저 표시됨을 검증한다.
- [ ] 작은 PDF 20회 click→first-page p95 <=5초; 큰 PDF/Office/HWP, 두 탭, 기존 용량 제한, 네트워크 단절, 취소, 종료 실패, 작업자 재시작/원복을 검증한다. 큰 파일이 제한을 넘으면 빠른 오류·정리를 검증한다.
- [ ] 5분 idle + 최소 15분 겹침 시험: Windows/WSL available 각각 >=1GiB; swap 증가 <=128MiB, 종료 swap <512MiB; 관련 cgroup peak <=limit의 90%; 새 OOM kill/WSL allocation error 0. 지속 부하가 남으면 동일 조건의 안정 구간까지 측정한다. 초과 시 라이브 배포하지 않는다.
- [ ] 같은 문서/부하의 before-after를 비교한다. 추가 작업자 idle 메모리는 관측값으로 보고하며 예상 100MB를 보장으로 사용하지 않는다. 진단/메모리 조정이 필요한 경우 범위를 분리해 보고한다.
- [ ] 구현 지시 이후 라이브 적용까지 승인된 경우에만 Task 5의 검증된 Apply로 전환한다. 현재 문서 작성은 그 승인이 아니다. live web/API 응답, preview claim 수신, 대표 원문 첫 페이지, 색인 정상 완료, 정리 ACK를 확인한다.
- [ ] 검증 결과·한계·원복 명령을 runbook에 기록하고 관련 파일만 커밋한다. 검증되지 않은 브라우저 표시나 모든 형식의 응답시간을 성공으로 보고하지 않는다.

## 완료 보고

보고에는 코드 커밋, PASS/FAIL과 미실행 항목, before/after 응답시간·최대 메모리, 실제 배포 여부, 원복 위치를 포함한다. 메모리 기준으로 배포를 보류했다면 그 사실을 첫 문장에 쓴다. 최종 독립 리뷰를 사용하는 경우에도 사용자 지정 모델 `gpt-6.1-sol/high`와 실행 방법을 존중하고, 요청되지 않은 별도 채팅을 생성하지 않는다.
