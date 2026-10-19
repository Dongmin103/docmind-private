# 여기서 시작하세요

2026-10-19 현재 배포본의 기준은 **비공개 저장소 `Dongmin103/docmind-private`의 `main`**입니다. 공개 저장소의 초기 브랜치 대신 이 저장소를 사용합니다. 먼저 [배포본 대조 기록](docs/DEPLOYED-SOURCE-BASELINE-2026-10-19.md), [기존 설정 가져오기](docs/CONFIGURATION-TRANSFER-2026-10-19.md), [GitHub 기반 배포 안내](docs/SERVER-DEPLOYMENT-2026-10-19.md)를 읽으세요.

1. [PRD.md](PRD.md): 최종 요구사항과 사용자 결정
2. [WINDOWS_DEVELOPMENT.md](WINDOWS_DEVELOPMENT.md): 받기·설정·서버 Codex 인수인계 문구
3. [현재 구조](docs/CURRENT-ARCHITECTURE.md): 코드와 서비스 연결
4. [인수인계 상태](docs/HANDOFF-STATUS.md): 포함/제외 및 검증 한계
5. [RAGFlow 축소 조사](docs/RAGFLOW-TRIM-AUDIT.md): 제거 후보와 공통 의존
6. [C 검색 계약](docs/C-SEARCH-CONTRACT.md): C128·prefix2400·Jina v3.5 이식 기준

실제 소스는 **app/** 아래에 있습니다. 원본 PDF, 운영 청크·벡터·DB·MinIO 파일은 포함하지 않았습니다. 모델 가중치·Docker 이미지·상용 복호화 프로그램도 별도입니다. 기존 API 키는 새 비공개 저장소의 암호화 설정 첨부파일에서 가져올 수 있습니다. 공개 저장소의 9월 복구 릴리스는 이번 이전의 입력이 아닙니다. 서버 경로와 연결 주소는 대상 환경에 맞춰야 합니다.
