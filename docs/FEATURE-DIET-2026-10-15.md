# DocMind 기능 정리 검증·복구 기록

## 범위와 현재 상태

사용자가 승인한 1·2·3순위 기능 정리는 별도 관리자/CLI, 플러그인, 외부 메시징, 범용 앱, 에이전트·워크플로, 장기 메모리, 그래프 기능, MCP, 범용 데이터셋 편집, 미사용 모델 공급자·대체 저장소·Go 실행 경로를 포함한다. 현재 소스 기준은 `dd9672459a994f975213012a682324dd31be4780`다. 각 구현 단위의 리뷰와 전체 변경 리뷰에서 요구한 수정의 독립 재검토를 통과했다. 최종 이미지 실행 검증은 진행 중이며 배포 완료 기록이 아니다.

문서 동기화, source/tenant 권한, 버전 fencing, 재시도·정리, 선택 OCR, 기존 파싱·청킹, GTE, C128/prefix2400/Jina v3.5/Top5, 일반 답변·근거·기록, 미리보기, 프로필·모델 설정은 유지 대상이다. 원본·운영 DB/ES/객체·모델 캐시는 삭제하거나 이관하지 않았다.

## 확인된 증거

| 검사 | 결과 | 해석 범위 |
|---|---|---|
| 각 기능 및 전체 변경 수정의 독립 리뷰 | 통과 | 최종 이미지 실행 검증은 별도 |
| 최종 생산 Python 구문 | 297개 파일, 오류 0 | 실제 import/실행 검증과 구분 |
| 기존 제거 runtime 재유입 검사 | 통과 | 기존 catalog 검사이며 이번 모든 기능의 완전 제거를 단독 증명하지 않음 |
| Python lock 및 새 환경 설치 | 704→270개 lock 항목, 플랫폼 패키지 250개 설치 | 유지 패키지 버전 변경 없음; NumPy 1.26.4 실제 컴파일 |
| 새 npm 의존성·운영 화면 빌드 | 설치 및 Vite 빌드 통과 | 최종 빌드 27.45초; 기존 경고 존재 |
| 동일 TypeScript 환경 전후 비교 | 198→29개 기존 진단, 신규 0 | 전체 typecheck는 기존 오류로 exit 2 |
| 실제 격리 MySQL | 21개 단언 통과 | 56개 테이블, 선택한 과거 스키마, 보존 sentinel, source 동시성·rollback·fencing |
| 새 Python 환경 API bootstrap | 설정·ORM·전체 blueprint·모델 registry, 유지 route 11개와 제거 URL 404 4개 통과 | Redis 실제 실행; ES/MinIO 생성자는 대역, MySQL 연결 차단. 최종 전체 서비스 검증과 구분 |
| 모델 종류 병합 수정 | migration/catalog 53개 통과 | 삭제된/알 수 없는 모델 종류는 DDL 전에 명시적 실패; 실제 운영 migration 미실행 |
| 유지 파서 수정 검사 | 16개 통과 | text/table/empty-input 계약; 모든 실제 문서의 종단간 재색인 결과가 아님 |
| Helm 배포 구성 | 실제 렌더링 6개 및 lint 통과 | Elasticsearch 기본값, 삭제된 엔진 거부, MySQL/Redis/MinIO 유지; 클러스터 배포 미실행 |
| 과거 tenant 열의 ORM 기본값 | 실제 SQLite 검사 2개 통과 | SQL 기본값 없는 NOT NULL 열에 새 행 생성, 기존 비어 있지 않은 값 보존 |
| 첫 clean image (`4114d147`) | 실제 Dockerfile 빌드 및 실제 DB/ES/Redis/MinIO health 통과 | 최종 수정 전 이미지; 기본 연결의 새 공용 계정 생성도 성공 |

전체 리뷰의 초기 "기본 환경에서 새 계정 생성 실패" 판단은 실제 실행으로 정정했다. 첫 이미지의 애플리케이션 연결은 `PIPES_AS_CONCAT` 모드이며 계정 생성에 성공했다. 같은 격리 시험 DB에서 시험 프로세스의 연결만 `STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION`으로 바꾸면 실제 `create_new_user`가 MySQL 1364(`asr_id` 기본값 없음)를 반환했다. 서버 전체나 운영 설정은 바꾸지 않았다. 최종 수정은 이 조건에서도 새 행을 만들 수 있도록 ORM에 빈 기본값을 명시하며, ASR 실행 기능을 복원하지 않는다.

세부 실행 기록과 source hash는 이 작업의 격리된 `.superpowers/sdd/2026-10-14-docmind-feature-diet/`에 있다. 전체 요구사항과 단계별 증거는 [구현 계획](superpowers/plans/2026-10-14-docmind-feature-diet.md)에 기록한다.

## 메모리 해석

첫 화면 빌드는 Node heap 1,536MiB·컨테이너 2GiB 한도에 걸려 실패했다. Node heap 3,072MiB·컨테이너 4GiB에서 성공했다. 이것은 빌드 제한과 관련된 결과이며 운영 메모리 증가의 증거가 아니다. 한도는 실제 사용량과 다르다.

현재 운영 버전은 `docmind-ragflow:lazy-backends-20261014`이며 이번 정리 버전은 배포하지 않았다. 파일·의존성 감소만으로 RAM 절감량을 계산하지 않는다. 수치를 제시하려면 같은 데이터·설정·동시성에서 빌드 종료 후 idle/API/system 사용량을 비교해야 한다. 이번 정리의 색인 시간 개선이나 GPU 사용률 개선도 아직 주장하지 않는다.

## 복구 준비

2026-10-15 읽기 전용 확인 시 운영 컨테이너는 `docmind-windows-dev-ragflow-cpu-1`, Compose 서비스는 `ragflow-cpu`, 프로젝트는 `docmind-windows-dev`였다. 복구 기준 이미지와 소스는 다음과 같다.

- 이미지: `sha256:95ce5f04b2dce00ac6ef9605a9098a30b15f4730bcbeb2cd72e3cd353dd803fd`
- 태그: `docmind-ragflow:lazy-backends-20261014`
- 이미지 소스: `9447a9be0f4f8d4e3007c5e91e34963021e5939f`
- 현재 배포 구성: `C:/DocMindDev/docmind/.local/surplus-cleanup-deploy-20261014/release-compose.private.json`

위 private 구성 파일은 현재 존재하며 위 태그를 참조한다. 비밀값을 포함하므로 Git에 넣거나 내용을 출력하지 않는다. 새 배포 구성을 이 파일에 덮어쓰지 않는다. 현재 entrypoint와 service template의 bind mount도 함께 보존해야 하며 이미지 태그만 되돌리면 충분하다고 가정하지 않는다.

향후 배포 또는 복구 승인을 받은 경우에만 아래 절차를 실행한다. 이 정리 작업에서는 실행하지 않았다.

1. 새 작업 claim을 멈추고 진행 중 문서의 종료·임시자료 정리를 확인한다. 원본이나 색인을 초기화하지 않는다.
2. 보존된 private 구성의 이미지·bind mount가 위 복구 기준과 일치하는지 확인한다. 태그의 이미지 ID가 달라졌다면 저장한 immutable ID를 지정한 별도 복구 override를 사용한다.
3. API 서비스만 보존된 구성으로 재생성한다. DB·검색·객체·파서·GPU 서비스를 재생성하거나 볼륨을 삭제하지 않는다.

   ```powershell
   wsl.exe -d Ubuntu --exec /usr/bin/docker compose `
     -p docmind-windows-dev `
     -f /mnt/c/DocMindDev/docmind/.local/surplus-cleanup-deploy-20261014/release-compose.private.json `
     up -d --no-deps --no-build --pull never ragflow-cpu
   ```

4. `http://localhost:19380/api/v1/system/healthz`와 `http://localhost:18080/docmind`, 기존 source 권한·검색·청크 미리보기·모델 설정을 확인한다. 문서/벡터 수와 활성 버전을 전환 직전 기록과 비교한 후 worker를 재개한다.
5. 앱 소스 정리를 되돌리기 위해 운영 DB를 복원하거나 삭제된 기능 테이블을 지우지 않는다. 과거 모델 종류가 남은 migration은 명시적으로 실패하므로, 오류가 나면 자동 재작성하지 않고 보존한 설정·데이터를 기준으로 원인을 확인한다.

이 절차는 소스 검토와 현재 배포 식별에 근거한 복구 준비다. 실제 새 버전 배포·장애·복구 시험을 완료했다는 뜻은 아니다.
