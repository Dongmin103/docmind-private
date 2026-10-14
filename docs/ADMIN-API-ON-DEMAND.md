# 관리자 API 필요 시 실행

Windows 개발 배포는 기본 API만 자동 실행한다. 관리자 API는 별도 관리자
CLI/API에서 계정·서비스·시스템을 관리할 때만 켜고, 작업이 끝나면 끈다.
문서 검색·인덱싱·미리보기·일반 모델 설정은 기본 API에서 처리한다.

아래 명령은 현재 `Ubuntu` WSL의 `docmind-windows-dev-ragflow-cpu-1`
컨테이너에 적용한다. PowerShell에서 실행하며, 관리자 API의 컨테이너 포트는
9381, Windows loopback 포트는 19381이다. 기본 API 포트는 각각 9380/19380이다.

## 시작

```powershell
wsl.exe -d Ubuntu --exec docker exec -d -w /ragflow docmind-windows-dev-ragflow-cpu-1 sh -c 'exec flock -n -F /tmp/docmind-admin-api.lock python3 admin/server/admin_server.py >> /ragflow/logs/admin-on-demand.log 2>&1'
```

`flock`은 이 명령을 중복 실행해도 관리자 프로세스가 여러 개 뜨지 않게 한다.
명령이 반환돼도 초기화는 진행 중일 수 있다. 아래 상태 확인이 성공한 후
관리자 CLI를 사용한다. 로그는 컨테이너의 `/ragflow/logs/admin-on-demand.log`와
기존 관리자 로그에 기록된다. 로그에는 설정 정보가 포함될 수 있으므로 공개하지 않는다.

## 상태 확인

```powershell
wsl.exe -d Ubuntu --exec docker exec docmind-windows-dev-ragflow-cpu-1 curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9381/api/v1/admin/ping
```

실행 중이면 `pong` 응답을 받는다. 중지 상태나 초기화 중에는 연결이 실패한다.
관리자 인증·권한 정책은 기존 설정을 그대로 사용한다.

## 종료

관리자 요청이 끝난 뒤 실행한다.

```powershell
wsl.exe -d Ubuntu --exec docker exec docmind-windows-dev-ragflow-cpu-1 pkill -TERM -f '^python3 admin/server/admin_server.py$'
```

정확히 해당 명령으로 실행한 관리자 Python 프로세스만 종료한다. 이미 꺼져 있으면
종료 코드 1을 반환한다. 기본 API와 수집·미리보기 워커는 계속 실행된다.
컨테이너를 재시작하면 관리자 API는 다시 꺼진 상태가 된다. 관리자 프로세스에
자동 재시작 루프는 없으며, 비정상 종료했다면 상태를 확인하고 시작 명령을 다시 실행한다.

## 기본 시작 설정과 복구

- `app/docker/docker-compose-windows-dev.yml`과 수집 복구 overlay에서
  `--enable-adminserver` 옵션을 제거했다. 관리자 포트의 기존 loopback 매핑은 유지한다.
- 이미 만들어진 컨테이너에는 Compose 파일 수정만으로 적용되지 않는다. 진행 작업과
  평문 정리를 drain한 뒤, 현재 배포에 사용한 전체 설정으로 API 서비스만 재생성해야 한다.
  기본 Compose만 실행하면 배포 overlay가 사라질 수 있으므로 사용하지 않는다.
- 이전 배포에서 관리자 자동 재시작 루프가 살아 있는 동안에는 위 종료 명령을 사용하지 않는다.
  컨테이너 실행 인수에 `--enable-adminserver`가 없는 상태에서 사용한다.
- 상시 실행으로 되돌리려면 실제 배포 Compose의 API `command`에
  `--enable-adminserver`를 다시 넣고, 같은 drain·API 재생성 절차로 적용한다.
- 이 변경은 Windows 개발 배포에 한정한다. 별도 Linux/Go 배포 Compose의 기본값은 바꾸지 않는다.

## 적용·검증 기록 — 2026-10-14

- 현재 배포의 전체 Compose를 보존한 상태에서 관리자 실행 인수만 제거해
  API 컨테이너를 재생성했다. 배포 이미지·환경변수 값·mount·포트·메모리/CPU 한도는 같다.
  현재 배포 파일과 이전 배포 도구의 실행 설정 모두 기본 중지로 반영했다.
- 관리자 시작 후 `/api/v1/admin/ping`의 `pong`을 확인하고 종료했다. 초기화는 약 21초였다.
  시작·종료 전후 기본 API 프로세스 PID는 동일했고, 최종 관리자 프로세스와 9381 listener는 없다.
- 관리자 실행 중 PSS는 866.9MiB였다. 관리자만 종료한 비교에서 컨테이너 cgroup 메모리는
  **789.2MiB 감소**, Docker 표시 사용량은 **1.974GiB → 1.204GiB**였다.
  공유 메모리 귀속·캐시 산정 때문에 PSS와 컨테이너 절감량은 동일하지 않다.
  최초 API 재시작으로 줄어든 메모리는 이 절감량 계산에 포함하지 않았다.
- 기본 API·GPU·파서 상태, 합성 임베딩 6개, 로컬/공개 프런트 응답·해시,
  미인증 워커 요청 차단을 확인했다. 로그인 세션으로 문서 223개와 모델 목록을 조회하고,
  실제 검색에서 dense 후보 128개·최종 결과 5개를 확인했다. 원문·검색 내용은 기록하지 않았다.
- 수집·미리보기·원본 감시 작업은 모두 `Running`이다. 정상 문서 223개/12,460벡터,
  암호화 원본 244개 해시·크기·수정 시각, 임시 파일 0개, 정리 대기 0개를 확인했다.
- OCR 및 전체 재색인을 다시 실행하지 않았다. 인덱싱 시간 단축·최대 RAM 개선이나
  새 미리보기 생성 전체 흐름을 이번 검증 결과로 주장하지 않는다.

기계 판독 기록: [admin-api-on-demand-2026-10-14.json](measurements/admin-api-on-demand-2026-10-14.json).

## 관리자 중지 상태 전체 재색인 1회 — 2026-10-14 19:27

후속 사용자 요청으로 같은 암호화 원본 244개(280,831,352바이트)를 1회 재측정했다.
원본 해시·크기·수정 시각이 이전 시험과 모두 같고, 파생 색인만 초기화했다.
OCR은 요청하지 않았다. 문서 4/파서 2/파서 CPU 4코어/GPU 추론 1/짧은 배치 16/
채움 비율 85% 설정을 유지했다. 이번 측정 직전 API 재시작이나 캐시 반환은 하지 않았다.

| 항목 | 이전 관리자 실행 상태 | 이번 관리자 중지 상태 |
|---|---:|---:|
| 워커 시작 → 마지막 정리 ACK | 276.576초 | **267.398초** |
| GPU 평균 / 최대 | 57.72% / 100% | 59.58% / 100% |
| Windows RAM 최대 사용률 | 99% | 98% |
| Windows RAM 최소 가용량 | 5.9MiB | 287.6MiB |
| API cgroup 샘플 최대 | 3.447GiB | 3.028GiB |
| 정상 문서 / 벡터 | 223 / 12,460 | 223 / 12,460 |

이번에는 **9.178초(3.32%) 단축**됐다. 순수 문서 처리 구간은 263.910초,
상태 폴링으로 종료를 관찰한 시각은 269.193초이며, 비교 기준은 두 시험 모두
워커 시작부터 마지막 호스트 정리 ACK까지다.

기존 제외 사유는 동일하게 OCR 필요 PDF 18개·형식 불일치 2개·손상 HWP 1개이고,
새 처리 오류·OOM·메모리 한도 도달·측정 누락은 0이다. 손상 HWP는 스케줄러가
RETRY_WAIT로 옮겼지만 첫 시도 기록에서 기존 오류를 확인했으며 전체 244개 모두
각각 정확히 1회 시도했다. 원본 244개와 SQL/ES 활성 청크·벡터 정합성,
임시 파일 0개·정리 대기 0개를 검증했다. 수집·미리보기·감시 작업은 모두 복구했다.

단회 결과이며 호스트의 다른 작업·캐시·메모리 할당 상태를 통제하지 않았으므로,
9.2초 개선 전부를 관리자 중지 효과로 단정하지 않는다. RAM은 여전히 최대 98%였다.
[상세 측정 JSON](measurements/full-reindex-noocr-admin-off1-2026-10-14.json).
