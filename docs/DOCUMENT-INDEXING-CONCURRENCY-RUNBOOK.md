2026-10-13 사용자 배포 승인 구성은 문서 4개·파서 2개·CPU 한도 4코어, GPU 추론 1개·짧은 배치 최대 16/토큰 채움률 85%, 기존 처리 순서다. 아래 초기 시험 절차와 구분한다. 최선 표본은 20.17초(14문서, 885벡터, 단회)이며 반복 성능 보장은 아니다.

# 문서 병렬 인덱싱 시험·전환 절차

이 문서는 최초 문서 슬롯 2/파서 1 후보의 절차다. 후속 CPU 풀 후보는 문서 슬롯 최대 3/파서 프로세스 최대 2를 지원한다. 최신 설정과 **구성별 실측 1회** 결과는 [CPU 풀 324 결과](CPU-PARSER-324-RESULTS-2026-10-13.md)를 따른다. 아래 3회/중앙값 절차는 최초 제안이며 이번 CPU 풀 시험에는 적용하지 않았다.

2026-10-13 구현 후보. 기본값은 문서 1개다. 실제 서비스의 설정·실행본은 이 구현 작업에서 변경하지 않았다. 전체 파이프라인 성능/정합성 합격 전 상시 슬롯 2를 켜지 않는다.

## 실행 계약

- API **한 프로세스**에 `DOCMIND_INGESTION_DOCUMENT_CONCURRENCY=2`를 전달해야 슬롯 2 claim을 허용한다. 기본은 1이며, 잘못된 풀 크기는 거부한다. 여러 API 프로세스/호스트에 걸친 전역 제한은 구현 범위 밖이다.
- `MAX_CONCURRENT_EMBEDDING_REQUESTS=2`는 모든 cloud-ingestion 이벤트 루프가 공유한다. GTE 768, 배치 32, 기존 배치 동시 요청 2, GPU 모델 추론 1은 고정한다.
- Kordoc `/v1/parse`는 동시에 한 작업만 처리하며 추가 요청을 `503 PARSER_BUSY`로 거부한다. `kordoc_pilot.py`의 프로세스 공통·서비스 URL별 파싱 슬롯으로 요청/응답 읽기를 직렬화한다. 슬롯 대기 시간은 HTTP 제한 시간에서 차감한다. 슬롯은 임베딩 전에 반환되므로 A 임베딩과 B 파싱은 겹칠 수 있다. 다른 API 프로세스/호출자까지 조율하는 전역 큐는 아니다.
- 문서 준비·파싱·임베딩 단계를 겹치되 artifact 요청은 인덱스 활성화와 서버 정리가 끝날 때까지 응답을 기다린다. 별도 평문 대기 큐는 없다.
- 실제 복호화는 ingest/preview 공통 gate 1을 유지한다. OCR 작업은 다른 진행 문서가 없을 때만 claim하고, OCR가 진행 중이면 일반 문서를 추가 claim하지 않는다. 검색/preview GPU 사용량은 이 admission 제한에 포함되지 않는다.
- 감독 프로세스가 기존 ingest singleton을 보유하고 자식은 `worker_id-slot-0/1`, `work_root/slot-0/1`, `cleanup_receipt_root/slot-0/1`을 사용한다. preview 경로와 복호화 control root는 유지한다.
- 부모 강제 종료 시 Windows Job Object가 자식 트리를 종료한다. 강제 종료는 정상 drain과 다르며 기존 lease·reaper·receipt 복구 확인이 필요하다.

## 통제된 시험 배포

1. 시험 대상 manifest를 확정한다. 최소 두 형식과 작은/중간/큰 문서를 포함하고 원문 대신 해시·크기·형식·청크 수를 기록한다. OCR 없는 묶음부터 시작한다. 시험용 SQL/ES 또는 승인된 개발 대상에 한정한다.
2. 현재 ingest 실행 방식과 자동 재시작 주체를 확인한다. 새 claim을 막는 유지보수 상태를 먼저 만들고 기존 요청·host cleanup을 drain한다. 기존 실행이 `-Once`이면 종료를 기다린다. 계속 실행하는 구버전 워커는 idle 확인만으로 안전한 drain을 단정하지 않는다. 진행 중인 프로세스를 단순 강제 종료해 전환하지 않는다.
3. 활성 lifecycle/host cleanup PENDING 0, parser workspace 정리 완료, 기존 work/receipt root의 잔여 작업 0을 확인한다. standalone root와 slot root 사이에 미완료 작업을 이동하거나 지우지 않는다. 남은 receipt는 원래 실행본·worker identity로 ACK까지 마친다.
4. 기존 API 이미지·실행 명령·비밀 없는 설정 해시를 보존한다. 후보 API와 Windows 파일 묶음을 함께 배포한다. 파일 묶음에는 `DocMindIngestionPool.ps1`, `DocMindDecryptGate.ps1`, common helper, worker, pool entrypoint가 모두 필요하다. 구 API는 `ingestion_pool_size`를 거부하므로 워커만 먼저 켜지 않는다.
5. 단일 API 프로세스와 위 환경변수를 확인한다. 슬롯 1로 소규모 검증 후 슬롯 2 시험을 시작한다. 아래 경로는 예시이며 실제 비공개 설정·승인된 source ID를 넣는다. stop 파일은 실행마다 새 경로를 사용한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Start-DocMindIngestionPool.ps1 `
  -ConfigPath C:\private\ingest.json -Concurrency 2 `
  -StopFile C:\private\run-001.stop -ClaimSourceId approved-test-source `
  -TimingRoot C:\private\metrics\run-001
```

`-Once`는 슬롯마다 한 번만 claim하므로 전체 manifest 완료 확인을 대신하지 않는다. worker ID에 붙는 slot 접미사까지 128자 이내여야 한다.

## A/B 합격 판단

A=문서 1/복호화 1, B=문서 2/복호화 1. 같은 모델·청킹·배치·manifest·캐시 조건에서 A/B 순서를 교차해 각각 3회 수행한다. 시작부터 마지막 문서 검색 가시성 및 모든 정리 완료까지를 측정한다. 호스트 `TimingRoot`와 서버 `StorageAttemptAudit`의 단계별 단조 시각을 사용한다. 겹친 단계의 합계를 전체 시간으로 사용하지 않는다.

서버 감사 기록의 attempt 및 stage에 `started_ns`/`finished_ns`를 기록한다. `clock=perf_counter_ns`, `process_id`가 같은 단일 API 실행 안에서 문서 A의 `embedding`과 문서 B의 `parse_http`/전처리 구간이 실제로 겹치는지 비교한다. 프로세스 재시작 또는 서로 다른 호스트의 monotonic 값은 직접 비교하지 않는다. 임베딩 요청 구간 사이의 빈 시간과 nvidia-smi의 GPU 유휴 표본을 함께 본다. HTTP 임베딩 구간에는 요청 대기·전송도 포함되므로 이 구간 전체를 GPU 계산 시간으로 간주하지 않는다. 목표는 임베딩을 중복 실행하는 것이 아니라 준비 단계와 GPU 작업 사이의 대기를 줄이는 것이다.

`app/scripts/benchmark_docmind_indexing_concurrency.py`는 명시적으로 전달한 명령의 전체 시간과 장치 전체 GPU 사용량을 수집한다. 실행 명령이 검색 가능·정리 완료를 실제로 검사하고 실패 시 0이 아닌 코드로 종료해야 한다. 이 도구 자체가 문서를 재인덱싱하거나 성공 상태를 검증하지 않는다. timeout은 실패이며 원격 작업의 종료/정리를 보장하지 않는다. 실패한 실행과 GPU 표본이 없는 실행은 비교에서 제외하고 원인을 해결한다.

```powershell
python app/scripts/benchmark_docmind_indexing_concurrency.py `
  --metadata C:\private\run-metadata.json --output C:\private\metrics\run.json `
  --timeout 3600 -- powershell -NoProfile -File C:\private\run-approved-indexing-and-verify.ps1
```

metadata에는 `manifest_sha256`, `model`, `batch`, 코드 버전, 문서/복호화 동시성, 실행 번호를 넣는다. 키·원문·벡터·인증정보는 넣지 않는다. 예시 실행 명령은 시험 대상별로 작성해야 하며 제공된 제품 스크립트가 아니다.

격리 ES의 빈 인덱스를 매회 초기화할 때에는 기존 서비스의 인덱스 매핑·설정을 읽어 동일하게 생성한다. 신규 동적 매핑 생성 실패를 처리량 차이로 계산하지 않는다. Python에서 Windows PowerShell 5.1을 자식으로 실행할 경우 `PSModulePath`에 PowerShell 7 모듈 경로가 섞이지 않도록 실행 환경을 분리한다. 상태 조회의 일시적인 연결 종료와 실제 문서 실패는 구분해 기록한다.

필수 비교 항목: 전체 시간 중앙값, 성공 처리량, 문서 지연 p50/p95, 첫 문서 검색 가능 시간, 단계별 수행/대기, GPU 평균/최대/유휴 비율, VRAM·RAM·임시 디스크 최대, 오류·timeout·retry·정리 누락. GPU 표본의 측정 간격과 시간축 오차도 기록한다.

별도 SQL/ES 시험에서는 두 claim 경쟁, 구세대/신세대 완료 역전, 처리 중 원본 수정/삭제, lease 만료, activation 실패, 응답 유실, 부모/슬롯 강제 종료를 재현한다. 최신 세대만 검색되고 중복 청크·정리 누락이 없어야 한다. 큰 문서 두 개 및 OCR/검색 혼합 부하를 추가한다.

적용 기준은 전체 시간 중앙값 10% 이상 단축, 성공률·정합성 동일, OOM/정리 누락 0, 충분한 메모리 여유다. 검색 p95 악화는 20% 이내를 제안 기준으로 사용하되 표본이 부족하면 합격으로 표시하지 않는다. GPU 사용률만 높아진 것으로 채택하지 않는다.

## 중지·복구

```powershell
New-Item -ItemType File -Path C:\private\run-001.stop
```

stop 파일을 감지한 자식은 진행 작업을 마치고 다음 claim 전에 종료한다. 감독 프로세스도 새 자식을 만들지 않고 기다린다. 이미 날아간 claim 요청이 있으면 한 작업을 더 drain할 수 있다. 타임아웃/ACK 실패가 있으면 pool 종료만으로 정리 완료를 판단하지 않고 DB·workspace·receipt를 모두 확인한다.

슬롯 2→1 전환 전에 **두 슬롯 모두 drain하고 receipt를 원래 슬롯 identity로 ACK**한다. 잔여 slot-1 파일이 있으면 Concurrency 1로 바로 축소하지 않는다. 빈 상태 확인 후 새 stop 파일 경로로 슬롯 1을 실행한다. API 환경값도 1로 복구하려면 모든 요청이 끝난 뒤 보존한 실행 설정으로 재기동한다. standalone으로 복귀할 때도 동일하게 slot root가 비었는지 확인한다. 실패 작업을 임의로 COMPLETE 처리하지 않는다.

복호화 2는 별도 변경이다. 한 known-pair의 동시 성공만으로 gate mutex를 제거하지 않는다. 서로 다른 크기·형식의 실제 EXE 검증과 B 대비 전체 시간 이득, 두 프로세스의 소유·종료·정리 계약을 먼저 충족해야 한다.
