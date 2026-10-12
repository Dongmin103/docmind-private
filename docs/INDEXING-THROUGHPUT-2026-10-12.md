# 전체 인덱싱 대기 개선 검증 기록

날짜: 2026-10-12 (Asia/Seoul)
브랜치: `perf/indexing-throughput`
변경 전 기준: `8a3bbc9`
설계: `docs/superpowers/specs/2026-10-12-indexing-throughput-design.md`
계획: `docs/superpowers/plans/2026-10-12-indexing-throughput.md`

## 구현 결과

- 호스트 워커는 정상 처리·정리·상태 ACK 후 다음 작업을 즉시 claim한다. NONZERO_EXIT/OUTPUT_MISSING/OUTPUT_EMPTY/PREVIEW_INPUT_TOO_LARGE도 정리와 상태 ACK가 확인된 경우에만 즉시 계속한다. 통신·인증·실행 기반시설 오류에는 polling 대기를 유지한다.
- 초기 스캔은 `EnqueueScan`으로 즉시 첫 확인 대상에 등록한다. 이미 처리 중인 항목의 세대·deadline·fingerprint·이동·삭제 정보를 덮어쓰지 않는다. 서버가 ACK한 보류 항목은 후속 스캔에서 다시 시도할 수 있다.
- 감시기는 source 방문당 due 항목 최대 32개를 처리한다. dirty 전달 이후부터 due 처리에 1초 예산을 적용한다. 예산은 항목 사이에서 검사하며 동기 HTTP·해시 호출을 중단하는 최대 수행 시간은 아니다.
- 이미 시간이 된 재확인을 우선한다. dirty 세대 경합으로 freeze가 실패해도 미확인 outbox가 없고 세션이 유효하면 관계없는 due 항목을 진행한다.
- 진행할 일이 있을 때 고정 polling 휴식을 제거했다. 세션·source 장애에는 source별 재시도 기한을 적용해 정상 source가 일하는 동안 장애 source를 빠르게 반복 호출하지 않는다.
- 스캔과 감시기의 기존 별도 프로세스를 유지하고, 스캔 완료 전에 안정된 파일을 처리한다. 전체 스캔이 성공하기 전 삭제 후보 확정은 허용하지 않는다.

## 측정

사용자 원본·운영 큐·실제 임베딩 API 없이 로컬 합성 파일과 서명 HTTP 서버로 측정했다. 아래 시간은 source 관찰/작업 등록 단계이며 복호화·파싱·임베딩·벡터 저장·검색 활성화를 포함한 전체 인덱싱 시간이 아니다.

| 시험 | 변경 전 | 변경 후 |
|---|---:|---:|
| 이미 120초 대기를 마친 12개 파일(각 27바이트), source 1개, poll 1초: 마지막 두 번째 관찰 수신 | 25.66초 | 11.64초 |
| 초기 스캔: 첫 번째 관찰 수신 | 120초 대기로 제한 시간 내 미수신 | 2.14초 |
| 초기 스캔: 첫 파일의 두 번째 관찰 수신 | 스캔 완료 전 미수신(회귀 실패 확인) | 12.21초 |
| 첫 배치 ACK를 15초 지연한 스캔 전체 완료 | — | 16.38초 |

12개 파일의 감시기 시험은 약 55% 단축됐다. 초기 스캔 시험에서는 첫 파일이 전체 스캔보다 먼저 두 번의 관찰을 마쳤고, 두 관찰 간 10초 이상 간격을 유지했다. 지원하지 않는 확장자를 포함한 4개 파일 전체가 존재 목록에 남았다. 결과는 단회 합성 비교이며 실제 문서 전체 처리의 단축률을 보장하지 않는다.

워커 루프를 실제 코드에서 추출해 실행한 시험은 준비된 연속 3개 작업 사이 sleep이 없고, 빈 큐에서만 한 번 sleep함을 확인했다. 실제 job 함수도 합성 실행 파일로 돌려, artifact HTTP 503/응답 인증 실패 뒤 평문 정리와 상태 ACK가 성공해도 원래 오류가 유지됨을 검증했다. loop는 이 경우 대기를 유지한다. 시험 mutex는 별도 이름을 사용해 실제 uEncryptor와 경쟁하지 않았다.

## 검증 명령과 결과

아래 7개 PowerShell 시험은 모두 통과했다. 실패 상황을 주입하는 시험의 WARNING은 예상된 결과다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindSyncQueue.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindSyncCollector.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindSyncThroughput.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindWorkerThroughput.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindUEncryptorHostWorker.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindIncrementalSync.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindScanThroughput.ps1
```

추가로 `Test-DocMindScanThroughput.ps1 -SchedulerBenchmark`와 `-SchedulerBenchmark -Baseline`을 실행했다. `-Baseline`만 지정하는 초기 스캔 회귀 시험은 변경 전 스크립트에서 예상대로 실패했다. Python `py_compile`과 `git diff --check`도 통과했다.

## 독립 리뷰와 보완

독립 읽기 전용 리뷰에서 4개 주요 지적을 받았다. 모두 실패 재현 후 수정하고 관련 시험을 통과했다.

1. 1초 이상 걸리는 dirty ACK 또는 dirty snapshot 세대 경합이 due 처리를 밀어내는 문제: due 예산 시작 시각과 freeze 실패 분기 보완.
2. 보류된 supported 파일이 설정 수정 후에도 스캔에서 재시도되지 않는 문제: ACK된 보류 항목만 이벤트 정보 보존 후 재활성화.
3. artifact 통신·인증 오류를 정리 성공만으로 문서 오류로 취급하는 문제: 즉시 계속할 수 있는 문서 오류를 명시적으로 제한.
4. 정상 source의 작업 때문에 다른 source 장애 재시도가 과도하게 빨라지는 문제: source별 재시도 기한 추가.

기존 호스트 self-test의 동기화·스캔 관련 문자열 검사는 예전 watcher 구조를 가리켰다. 현재 서명 `/changes`, native collector, 독립 scan 구조의 계약으로 갱신했다. 실제 부재 확인·서명·fencing·평문 정리 검사는 유지했다.

## 적용 범위와 남은 작업

이번 실행은 제품 소스와 테스트를 변경한 것이다. 실행 중인 서비스 재시작, `.local/file-sync-release-20261001` 파일 교체, 운영 배포, 실제 문서 전체 재인덱싱은 수행하지 않았다. 해당 실행 경로에 배포해야 실제 작업 시간이 바뀐다.

지원하지 않는 파일도 기존 등록 문서일 수 있어 서버 mapping 근거 없이 확장자만으로 전체 스캔 큐에서 제외하지 않았다. 이는 업데이트 누락 방지를 위한 결정이며 일부 불필요한 관찰 비용이 남는다. 새 브로커·추가 워커·복호화 병렬 실행은 도입하지 않았다.

다음 묶음은 실제 환경에서 전체 소요 시간과 요청별 임베딩 시간·토큰·재시도를 측정하고, 서비스·어댑터 배치 제한 확대와 제목/본문 요청 통합을 검증하는 것이다.
