# GitHub 기반 다른 서버 배포

2026-10-19 현재 실행 중인 검색 개선과 비동기 답변 코드는 **`feat/answer-jobs` 브랜치**에 있다. 저장소 기본 브랜치 `main`이나 초기 `windows-development-handoff` 대신 이 브랜치를 받는다. 검색 구현 커밋은 `59a3005`, 평가·기존 색인 보강 도구는 `d5cac9b`에 포함된다.

```bash
git clone --branch feat/answer-jobs --single-branch https://github.com/Dongmin103/docmind-recovery.git docmind
cd docmind
git rev-parse HEAD
```

실제 배포에 사용한 전체 SHA를 서버 배포 기록에 남긴다. 기존 checkout은 작업 파일과 현재 버전을 확인한 뒤 해당 브랜치를 fetch/checkout한다. 변경 파일을 강제로 덮어쓰는 reset은 필요하지 않다.

## 서버 구성과 이미지

Git에는 애플리케이션 소스, Dockerfile, 설정 예제, 호스트 워커 도구 및 검증 코드가 들어 있다. Docker 이미지·모델 가중치·상용 uEncryptor2·실제 설정과 키·원본·DB·벡터·객체 저장소 데이터는 별도로 준비해야 한다. 현재 PC의 `.local` 디렉터리를 다른 서버의 설정으로 복사하지 않는다.

새 Windows 서버의 기초 구성은 [Windows 개발 안내](../WINDOWS_DEVELOPMENT.md), [Docker 구성](WINDOWS-DEVELOPMENT-DOCKER.md), [GTE 구성](LOCAL-GTE-DEPLOYMENT-2026-10-12.md)을 참고한다. Windows 복호화 실행부와 Docker Linux 서비스의 역할을 유지한다. 기존 개발 Compose의 합성 데이터 보안 경계를 임의로 해제해 운영 구성으로 사용하지 않는다. 서버별 원본 경로, 계정, 비밀값, 모델 주소, 도메인과 TLS를 그 서버에서 설정한다.

전체 소스 이미지 빌드의 context는 `app/`이다. Docker Linux 환경에서 저장소 루트 기준 예시는 다음과 같다.

```bash
docker build -f app/Dockerfile -t docmind-ragflow:git-20261019 ./app
```

이 Dockerfile은 의존 이미지·패키지·모델 자원을 내려받고 프런트도 빌드한다. 해당 서버의 네트워크·메모리·가용 버전을 확인해야 한다. 현재 PC에서 검증한 배포는 기존 이미지에 변경 파일을 반영한 방식이며, 다른 서버에서 위 전체 빌드를 완료했다고 주장하는 기록은 아니다. `Dockerfile.search-documents` 등 과거의 일부 파일 전용 overlay만 빌드하면 이번 브랜치 전체가 반영되지 않는다.

새 이미지를 서버의 실제 Compose에 지정한다. 기존 설치라면 환경변수 값, 마운트, 키, 볼륨, 리소스 설정을 유지하며 API와 프런트를 함께 전환한다. 이 브랜치의 프런트는 비동기 답변 API를 사용한다. 별도 웹 호스팅을 사용하면 그 프런트도 같은 소스에서 빌드·배포한다. 답변 작업용 추가 테이블과 동작은 [답변 배포 기록](ANSWER-JOBS-2026-10-18.md)을 따른다.

## 기존 문서가 있는 서버의 제목·보조 색인 보강

새로 색인하는 문서는 코드가 제목·보조 필드를 생성한다. 이미 색인된 문서는 코드 교체만으로 채워지지 않으므로 `tools/backfill_docmind_search_fields.py`를 **대상 서버 설정과 새 코드가 로드된 환경**에서 실행한다. 재파싱·재임베딩은 필요하지 않다. 이 도구는 활성 source 카탈로그와 chunk-set ID가 있는 문서를 대상으로 한다. 카탈로그가 비었거나 이전 형식이면 처리 건수 0을 완료로 오인하지 말고 해당 서버의 카탈로그 구성을 먼저 확인한다.

아래는 Bash/WSL 예시다. `API_CONTAINER`는 대상 API 컨테이너명으로 바꾸고, `JOURNAL`은 **컨테이너 내부에서 쓰기 가능하고 호스트에 영구 마운트된 비공개 위치**로 정한다. journal에는 파생 텍스트가 포함되므로 Git 밖에 보관한다.

```bash
API_CONTAINER=your-docmind-api-container
JOURNAL=/your-persistent-private-mount/search-fields-20261019.private.jsonl
docker cp tools/backfill_docmind_search_fields.py "$API_CONTAINER":/tmp/backfill_docmind_search_fields.py

# 사전 조회: 변경 예상 건수 확인, journal은 생성하지 않음
docker exec -w /ragflow "$API_CONTAINER" /ragflow/.venv/bin/python /tmp/backfill_docmind_search_fields.py --journal "$JOURNAL"

# 적용: 동시에 바뀐 청크는 덮어쓰지 않고 충돌로 집계
docker exec -w /ragflow "$API_CONTAINER" /ragflow/.venv/bin/python /tmp/backfill_docmind_search_fields.py --journal "$JOURNAL" --apply

# 본문·벡터·기존 메타데이터 해시 보존 확인
docker exec -w /ragflow "$API_CONTAINER" /ragflow/.venv/bin/python /tmp/backfill_docmind_search_fields.py --journal "$JOURNAL" --verify
```

`--apply`는 기존 journal을 덮어쓰지 않는다. 반복 적용은 새 journal 경로를 사용한다. 충돌·통신 실패가 있으면 해당 레코드를 확인한다. 중단 직전의 적용 ACK가 유실된 intent는 별도 대조가 필요하다. `--verify`는 ACK가 기록된 청크의 보호 필드와 추가 필드 존재를 검증하며, 전체 카탈로그의 처리 누락까지 증명하는 명령은 아니다. 성공·충돌·변경 없음 건수를 예상 활성 청크 수와 함께 확인한다.

## 전환 확인과 복귀

1. DB, Elasticsearch, Redis, 스토리지, GTE와 Jina 연결을 확인한다. 임베딩 차원 768과 기존 벡터 필드를 유지한다.
2. 비로그인 검색 차단과 전체/폴더/문서 범위를 확인한다. BM25·dense 각 128, 통합 128, 문서당 8, 본문 prefix 2400, Jina v3.5, 최종 5개 계약을 확인한다.
3. 대상 서버 문서로 제목, 한국어 띄어쓰기, 제품·모델 번호를 조회한다. 검색과 답변의 범위·인용 버전을 확인하고 이전 버전과 비교한다. 이 PC의 36건 평가를 다른 데이터셋의 정확도로 간주하지 않는다.
4. 이전 이미지와 실행 설정을 보존한다. 프로그램 이미지만 되돌려도 기존 본문·벡터를 복원할 필요는 없다. 추가 색인 필드도 되돌릴 필요가 있을 때만 같은 도구에 `--rollback`을 사용한다. 이후 변경된 레코드는 버전 조건 때문에 복원이 거부된다. 문서 원본이나 DB 전체를 과거 백업으로 덮어쓰는 절차가 아니다.

현재 PC에서의 검증 수치와 한계는 [검색 개선 보고서](SEARCH-LEXICAL-2026-10-19.md)와 [평가 JSON](SEARCH-LEXICAL-2026-10-19.json)에 기록돼 있다.
