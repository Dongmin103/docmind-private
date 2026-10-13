# 문서 인덱싱 병렬 처리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 현재 요청은 계획 작성이며 실행·배포 승인이 아니다.

**Goal:** 같은 문서와 검색 결과를 유지하면서 문서 준비·임베딩·저장을 겹쳐 전체 인덱싱 시간을 줄이고, 복호화 자체의 병렬 실행 가능 여부도 실측한다.

**Architecture:** 첫 구현은 Windows 인덱싱 슬롯 두 개가 각각 기존의 동기식 artifact 요청을 수행하도록 한다. 완료 ACK·임시 평문 정리 계약은 유지하고 서버에는 슬롯 간 공통 자원 제한을 둔다. 복호화 동시성은 시험 전에는 1이며, 분리된 시험에서 안전성과 효과를 확인한 경우에만 별도 후속 변경으로 2를 적용한다.

**Tech Stack:** Windows PowerShell, uEncryptor2, Quart/Python, 기존 SQL 작업/lease/fencing, GTE CUDA 서비스, Elasticsearch.

**Spec:** 이 문서의 ‘설계 기준’이 이번 계획의 설계 초안이다. 이전 대화의 단계별 중첩 제안과 사용자의 복호화 병렬성 확인 요청을 반영했다. 구현 전 이 설계와 계획을 함께 검토한다.

**조사 기준:** 2026-10-13 KST, `C:/DocMindDev/docmind`, 소스 HEAD `8c9e8ed`. 실행 배포본과 HEAD의 일치는 구현 착수 시 다시 대조한다. 아래 파일 경로는 별도 표시가 없으면 이 저장소 기준이다. 기존 미커밋 변경을 덮어쓰지 않는다.

## 설계 기준

### 현재 확인한 구조와 측정 한계

- `Start-DocMindUEncryptorHostWorker.ps1`은 `Invoke-LeasedDecryptJob` 완료 이후 다음 작업을 claim한다.
- `upload_cloud_sync_artifact`는 `asyncio.to_thread(process_decrypted_artifact, ...)`를 기다린다. 서버 HTTP 처리는 비동기지만 호스트의 문서 공급은 직렬이다.
- `process_decrypted_artifact`는 파싱·임베딩·staging·활성화·파서 정리까지 요청 안에서 수행한다. 완료 ACK가 Windows 평문 정리의 경계다.
- 복호화 gate는 출력 검증 후, artifact 요청 전에 해제된다. 따라서 복호화 동시성이 1이어도 다음 문서의 복호화를 앞 문서의 임베딩과 겹칠 수 있다.
- `ProductionTemporaryParserInputRunner`는 요청마다 `asyncio.run`을 사용한다. `LoopLocalSemaphore`의 용량 2는 이벤트 루프별이며 문서 두 개 전체의 요청 상한 2를 뜻하지 않는다.
- GTE 배치 32 / 문서별 요청 동시성 2, GPU 서비스의 실제 추론 lock 1은 확인했다. 요청 배치 32와 모델 내부 추론 배치는 다르다.
- 앞선 문서 10개·1,548청크 재계산은 약 39초, GPU 평균 81%/최대 100%, 최대 VRAM 4,785MiB였다. 가장 큰 문서 한 개가 1,395청크를 차지하므로 대표 표본 평균으로 일반화하지 않는다.
- 위 시험은 임베딩만 재실행했으며 전체 인덱싱·OCR을 측정하지 않았다. 완료 이벤트 뒤 측정 자식 프로세스의 종료코드 1이 발생해, 성능 비교 기준선으로 쓰기 전에 측정기를 바로잡는다.
- 2026-10-12의 244문서 전체 시간 기록은 Cloudflare BGE 환경이다. 현재 GTE 비교군으로 사용하지 않는다.
- PRD의 복호화 1개 제한 근거는 단일 실행 mutex ‘단서’다. 실제 uEncryptor2 두 프로세스의 동시 성공/실패를 입증한 결과는 이번 조사에서 확인하지 않았다. 우리 코드의 `Global\\DocMind-uEncryptor2-SingleRun`은 자체 보호장치다.

### 접근 선택

1. **선택: 완료 ACK를 유지하는 문서 슬롯 2개.** 각 문서는 기존 실행 경로를 유지하면서 다른 문서의 단계와 겹친다. 호스트 경로·정리·동시 요청 제한을 보강해야 하지만 프로토콜 변경이 작다.
2. 수신 즉시 ACK 후 서버 백그라운드 큐로 전환: 임시 입력의 수명·재시작 복구·상태 조회 계약이 바뀐다. 첫 개선 범위에서 제외한다.
3. GPU 모델 프로세스/추론 수 증설: VRAM과 검색 지연 위험에 비해 이득이 미확인이다. 첫 비교에서 변경하지 않는다.

### 첫 비교 설정

| 항목 | 기준선 A | 비교안 B |
|---|---:|---:|
| 전체 진행 문서 슬롯 | 1 | 2 |
| uEncryptor2 동시 실행 | 1 | 1 |
| 문서 준비·파싱 | 최대 1 | 최대 2, 파서 자체 제한 존중 |
| 인덱싱 임베딩 요청 총수 | 2 | 두 슬롯 합계 2 |
| GTE 실제 추론 | 1 | 1 |
| 요청 배치 상한 | 32 | 32 |
| 추가 평문 대기 큐 | 없음 | 없음 |

두 진행 슬롯 자체가 처리량 제한이다. 문서 대기열을 별도로 두어 미리 복호화하지 않는다. 요청 body는 현재 최대 256MiB이며 파싱 시 추가 복사본과 해제 데이터가 생기므로, 슬롯 2를 ‘메모리 최대 512MiB’로 표현하지 않는다. RSS·호스트 RAM·임시 디스크 최대량을 측정한다. 입력 크기와 파서 기존 상한을 낮추거나 청크를 버려 성능을 확보하지 않는다.

첫 B 시험은 자동 OCR 없는 기존 경로를 대상으로 한다. OCR 선택 작업은 삭제하거나 건너뛰어 완료로 표시하지 않으며 기존 실행 대상으로 보존한다. 혼합 실행을 켜기 전에는 OCR 작업과 일반 인덱싱의 동시 admission을 막는 호스트 전체 제어와 회귀 검증을 추가한다. 검색 질의 임베딩까지 포함한 GPU 우선순위 스케줄러는 이번 첫 구현에 포함하지 않지만 검색 지연은 측정한다.

### Global Constraints

- 원본 변경 없음. GTE 768차원, 기존 청킹·제목 혼합·C128·prefix2400·Jina v3.5 계약 유지.
- 복호화 1은 시험 전 설정이다. 공급사 프로그램의 고유 제약이라고 단정하지 않는다.
- ACK 조기 반환 금지. 호스트/파서 정리 확인 전 완료 처리 금지.
- 같은 문서의 세대·fencing·lease 검증 및 원자적 활성화 유지.
- 원문·벡터·키는 측정 로그에 남기지 않는다. 문서 식별은 해시/실행 번호로 기록한다.
- 첫 병렬 배포는 기존 단일 API 프로세스 구성을 대상으로 한다. 프로세스 내부 limiter를 여러 API 프로세스/호스트의 전역 제한으로 설명하지 않는다.
- 서비스 재시작·실제 전체 재인덱싱은 계획 작성의 일부가 아니다.

## Review Focus

1. 슬롯별 reaper가 다른 슬롯의 실행 중 평문을 삭제하지 않아야 한다 → Task 3.
2. 다른 이벤트 루프의 임베딩 요청을 합쳐 상한 2를 지키고 취소 후 permit이 유출되지 않아야 한다 → Task 2.
3. 처리 중 원본 수정/삭제·lease 만료가 발생하면 이전 세대를 활성화하지 않아야 한다 → Task 4.
4. 슬롯 하나의 종료·ACK 유실·정리 실패 시 중복 실행과 임시파일 유실 없이 복구해야 한다 → Task 3, 4.
5. 큰 입력 두 개 및 OCR 선택 작업이 동시에 도착해도 자원 고갈·OCR 누락·검색 장기 지연이 없어야 한다 → Task 4, 5.

## Task 1: 복호화 병렬 가능성 시험과 현재 기준선 확립

**Files:** Create `tools/windows/Test-DocMindDecryptConcurrency.ps1`, `tools/windows/Test-DocMindDecryptConcurrencyProbe.ps1`, `app/scripts/benchmark_docmind_indexing_concurrency.py`. 기존 감사 경로 `app/common/storage_attempt_audit.py`와 호스트 `HostTimingJsonl` 재사용. 이전 측정기는 현재 작업 폴더의 `experiments/gpu-indexing-load/measure.py`.

**Interfaces:** 시험 CLI `-FixtureManifest <private-path> -Concurrency 1|2 -Repetitions 5`; 출력은 실행별 elapsed/exit/hash_match/overlap/cleanup 결과만. 성능 보고 JSON은 실행 ID, 고정 manifest 해시, 모델/설정/코드 버전, 단계별 monotonic 시간, GPU 표본, 오류 수를 포함한다.

- [ ] 시험 스크립트의 합성 실행기로 독립 출력 경로, 동시 실행 겹침 검증, 잘못된 결과 해시 거부, timeout 후 자식 종료 확인을 테스트한다. 실패 상태가 테스트에서 검출되는 것을 먼저 확인한다.
- [ ] 비민감 known-pair 암호문/평문 해시를 사용한다. 서로 다른 작은/중간/큰 파일과 최소 두 형식으로 순차 1/동시 2를 각 5회 비교한다. 같은 파일을 덮어쓰지 않는다.
- [ ] 실제 EXE 동시 시험은 기존 ingest/preview 복호화와 겹치지 않는 격리된 시험 시간에 수행한다. 시험 전 일반 gate를 확보하고, 그 안에서 시험 자식 두 개만 소유·추적한다. 공급사 mutex를 제거하거나 프로그램을 수정하지 않는다.
- [ ] 2개가 실제로 겹쳐 실행됐는지 프로세스 생존 구간으로 확인한다. 둘 다 성공·해시 일치·대화창 없음·정리 완료일 때에만 ‘해당 버전/표본에서 병렬 가능’으로 판정한다. 거부/내부 직렬화면 동시성 1 유지.
- [ ] GPU 측정기의 종료코드 1 원인을 확인하고 부모/자식 상태, stderr의 민감정보 없는 진단, 정상 종료를 검증한다. `time.perf_counter`로 길이를 측정하고 시간축 동기화 오차를 기록한다. 완료 marker만으로 정상 프로세스 종료를 덮어쓰지 않는다.
- [ ] 현행 GTE에서 전체 인덱싱 A를 측정한다. 시작은 고정 대상의 처리 개시 직전, 종료는 마지막 문서 검색 가능 및 모든 정리 확인. 전체 시간과 GPU 계산 구간을 분리한다.
- [ ] Probe 단위검사는 실제 EXE를 호출하지 않는다. 실제 성능 시험은 별도 수동 실행으로 분리하고 결과·측정 도구를 검토 가능한 커밋으로 남긴다.

**판정:** 동시 복호화 2의 성공률 100%와 모든 해시 일치는 필수. 속도 이득은 5회 중앙값으로 평가한다. 첫 B에서는 복호화 1을 유지해 문서 단계 중첩 효과를 분리한다. 통과한 경우에만 Task 6에서 2를 비교한다.

## Task 2: 여러 문서에 공통으로 적용되는 인덱싱 요청 제한

**Files:** Create `app/api/apps/services/docmind_ingestion_limits.py`, `app/test/unit_test/api/apps/services/test_docmind_ingestion_limits.py`; Modify `app/api/apps/services/docmind_ingestion_runtime.py`; Test `app/test/unit_test/api/apps/services/test_docmind_ingestion_runtime.py`, `app/test/unit_test/rag/svr/task_executor_refactor/test_embedding_throughput.py`.

**Interfaces:** `IngestionRequestLimiter(capacity: int)`는 `async acquire() -> bool`, `release() -> None`, async context manager를 제공한다. 모듈 단위 `embedding_requests` 인스턴스 용량은 `MAX_CONCURRENT_EMBEDDING_REQUESTS`(현재 2). cloud ingestion context의 embed limiter에 주입한다. 다른 경로의 `LoopLocalSemaphore` 계약은 유지한다.

- [ ] `test_two_event_loops_share_capacity_two`: 서로 다른 스레드/이벤트 루프에서 문서 두 개·각 요청 두 개를 실행하고 합산 peak가 2 이하임을 검증한다. 기존 loop-local 방식의 peak 4를 재현한다.
- [ ] `test_cancel_waiter_does_not_leak_permit`, `test_exception_releases_permit`, `test_repeated_closed_loops`를 추가한다.
- [ ] 대기 중 취소 가능한 thread-safe 제한을 구현한다. blocking acquire를 백그라운드에 남겨 취소 후 permit을 획득하는 방식은 피한다. asyncio 루프에 종속된 semaphore를 루프 간 공유하지 않는다.
- [ ] 기존 EmbeddingService의 실행 중 HTTP 호출 drain이 끝나기 전에 permit을 반환하지 않는지 확인한다. 문서별 요청 2와 전체 요청 2를 함께 검증한다.
- [ ] 위 세 테스트 파일을 앱 테스트 환경에서 `python -m pytest <files> -q`로 실행하고 모두 통과 후 해당 변경만 커밋한다.

## Task 3: 작업 공간이 분리된 Windows 문서 슬롯 2개

**Files:** Modify `tools/windows/Start-DocMindUEncryptorHostWorker.ps1`, `tools/windows/DocMindUEncryptorHostWorker.Common.ps1`; Create `tools/windows/Start-DocMindIngestionPool.ps1`, `tools/windows/Test-DocMindIngestionPool.ps1`; Test 기존 `Test-DocMindWorkerThroughput.ps1`, `Test-DocMindPreviewWorkerIsolation.ps1`, `Test-DocMindDecryptGate.ps1`.

**Interfaces:** pool entry point `-ConfigPath <path> -Concurrency 1|2`, 기본 1. 각 ingest child는 `-IngestionSlot 0|1`로 실행한다. `Get-DocMindWorkerRolePaths`와 singleton이 슬롯을 반영한다. worker ID는 길이 제한을 검증한 고유 slot ID를 사용한다. preview ID/경로는 유지한다.

- [ ] 두 슬롯이 서로 다른 job을 claim하고, A의 artifact 응답 대기 중 B가 복호화할 수 있는 합성 테스트를 먼저 만든다.
- [ ] 슬롯별 work/receipt root와 singleton을 분리한다. decrypt control root와 실제 EXE 제한 1은 공유한다. 기존 단일 ingest 실행과 pool 실행은 상위 singleton으로 충돌을 막는다.
- [ ] 한 슬롯의 reaper·receipt replay가 다른 슬롯의 디렉터리에 접근하지 않는 테스트를 추가한다. 기존 단일 슬롯 잔여 파일은 소유 확인 후 기존 복구 경로로만 정리한다.
- [ ] 부모 중복 실행 및 부모 종료 후 orphan child가 새 pool과 겹치지 않는 수명 관리, 슬롯별 재시작 지연, 중단 시 claim 중지 후 진행 작업 drain을 구현한다. 무한 무지연 재시작 금지.
- [ ] 위 PowerShell 회귀 스크립트들을 `powershell -NoProfile -File <path>`로 실행한다. 실패 주입으로 ACK 유실·slot 종료·복호화 timeout을 검증하고 관련 변경만 커밋한다.

## Task 4: 병렬 실행의 정합성·자원·OCR 경계 검증

**Files:** Test `app/test/unit_test/api/apps/services/test_docmind_ingestion_service.py`, `test_docmind_ingestion_runtime.py`, `app/test/unit_test/api/apps/restful_apis/test_docmind_api.py`, `app/test/unit_test/rag/parser_platform/test_ephemeral_input.py`; Modify 위 테스트가 가리키는 ingestion 서비스와 pool의 admission 부분만.

**Interfaces:** 기존 `claim_next`, `process_decrypted_artifact`, signed ACK 구조 및 상태 전이를 유지한다. 원본 문서별 최신 세대만 활성화하며 서로 다른 문서의 작업은 독립적이다.

- [ ] 두 claim의 같은 job 획득 경쟁, 동일 문서 구세대/신세대 완료 순서 역전, 처리 중 삭제, lease 만료, activation 실패를 동시성 barrier가 있는 테스트로 재현한다.
- [ ] 단순 mock 성공뿐 아니라 별도 개발 SQL/ES에서 CAS·트랜잭션 경계를 검증하는 수동 통합 시나리오를 추가한다. 잘못된 활성 세대/중복 청크/정리 누락이 0인지 검사한다.
- [ ] 큰 입력 두 개, 한 슬롯 파서 오류, 취소 중 HTTP 실행 지속을 시험한다. 다른 슬롯의 workspace 삭제나 잘못된 성공 ACK가 없어야 한다.
- [ ] OCR 요청은 인덱싱 슬롯 전체에서 단독 실행되도록 claim 전 admission을 설계·검증한 뒤 혼합 모드를 연다. 기존 원자적 claim 경계에서 active 작업을 재확인하고, slot별 독립 lock만으로 OCR 배제를 구현하지 않는다. OCR 완료까지 신규 일반 claim을 제한하며 오래된 OCR 요청이 계속 밀리지 않게 한다.
- [ ] GPU OCR와 검색 임베딩의 동시 부하를 별도 시험한다. 합격 전 OCR 혼합 병렬 설정을 배포하지 않는다. 기존 OCR 동의·미리보기 격리 정책을 유지한다.
- [ ] 위 좁은 pytest/PowerShell 회귀 및 수동 통합을 통과하고 변경·결과를 커밋한다.

## Task 5: 같은 문서 묶음으로 A/B 측정 및 적용 판단

**Files:** Task 1 측정기, Create `docs/DOCUMENT-INDEXING-CONCURRENCY-RESULTS-2026-10-13.md`, `docs/DOCUMENT-INDEXING-CONCURRENCY-RUNBOOK.md` (날짜는 실제 실행일로 조정).

**Interfaces:** A=문서 1/복호화 1, B=문서 2/복호화 1. 모델과 요청 배치는 고정한다. A/B 순서를 교차하고 같은 파일·상태·캐시 조건에서 각각 3회 측정한다. 시험 결과 문서는 본문 없는 집계만 포함한다.

- [ ] 대표 문서 묶음은 작은/중간/큰 문서 및 실제 사용 형식을 포함한다. 이전의 대형 문서 편중 표본과 분리하고 형식·크기·청크 분포를 함께 기록한다.
- [ ] 성공 처리량, 전체 elapsed, 첫 문서 검색 가능 시간, 문서 지연 p50/p95, 단계별 수행/대기, GPU 평균/최대/유휴 비율, VRAM·RAM·임시 디스크 최대, 오류·timeout·retry·정리 수를 비교한다.
- [ ] 동일한 검색 부하를 별도 시나리오로 추가하고 검색 p95 악화가 20% 이내인지 평가한다. 표본 수가 부족하면 p95 합격을 주장하지 않는다. 이 수치는 제안된 적용 기준이지 기존 SLA가 아니다.
- [ ] 제안 적용 기준: 전체 시간 중앙값 10% 이상 단축, 성공률·정합성 동일, OOM 0, 정리 누락 0, 충분한 메모리 여유. GPU 사용률 상승만으로 성공 판정하지 않는다.
- [ ] 실패 시 병목을 단계별로 기록하고 기본 슬롯 1을 유지한다. 3·4개 슬롯으로 자동 확대하지 않는다.
- [ ] 적용 절차는 새 claim 중지 → 기존 작업·정리 drain → 설정/실행본 보존 → 검증된 슬롯 2 배포 → 소규모 확인. 복구는 동일하게 drain 후 슬롯 1로 복원하며 미완료 작업을 임의로 COMPLETE 처리하지 않는다.

## Task 6: 복호화 2개는 가능성과 이득이 입증된 경우에만 후속 적용

**Files:** `tools/windows/DocMindDecryptGate.ps1`, `Test-DocMindDecryptGate.ps1`, Task 3 pool 및 테스트. Task 1 결과가 이 작업의 필수 입력이다.

- [ ] Task 1에서 실제 동시 성공과 성능 이득을 확인한 경우에만 진행한다. 결과가 미확인/실패면 이 작업을 실행하지 않고 이유를 기록한다.
- [ ] 현재 gate의 단일 `owner.json`을 그대로 두고 mutex만 제거하지 않는다. 각 실행의 소유 PID/시작시각/EXE 해시·timeout·정리·부모 사망을 독립 추적하는 슬롯 소유 계약을 먼저 설계한다.
- [ ] ingest와 preview를 합한 호스트 전체 decrypt 상한 2, 슬롯 하나의 종료 미확인 시 안전한 claim 중단, PID 재사용 보호, preview 우선 정책을 회귀 테스트한다.
- [ ] B=문서 2/복호화 1과 C=문서 2/복호화 2를 같은 조건으로 비교한다. 복호화 단계만 빨라도 전체 시간 이득이 없으면 설정 1을 유지한다.

## 계획 검토 결과와 실행 순서

기준선 신뢰성 → 공통 요청 제한 → 두 문서 슬롯 → 정합성/OCR 검증 → 전체 A/B → 조건부 복호화 2 순서다. 제품 변경이나 배포는 아직 수행하지 않았다. Task 6은 현재 미입증인 공급사 동시 실행 가능성에 명시적으로 의존한다. 문서 병렬 처리 자체는 Task 6 성공에 의존하지 않는다.
