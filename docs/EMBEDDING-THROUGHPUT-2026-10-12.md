# 임베딩 처리량 개선 검증 (우선순위 5–7)

## 변경 범위

`EmbeddingService → LLMBundle.encode → OpenAIEmbed/OpenAI_APIEmbed` 경로에서 서비스와 실제 공급자 요청의 배치 크기를 함께 조정한다. 제목은 첫 요청의 첫 항목이며 배치 크기에 포함한다. 본문은 기존 `max_length - 10` 전처리를 유지하고 제목은 기존처럼 LLMBundle의 전처리를 거친다. 첫 제목 벡터를 모든 본문에 기존 가중치로 혼합한다. 기존 `filename_embd_weight=0`의 기본 가중치 0.1 처리도 유지한다.

컨트롤러의 읽기 전용 확인에서 현재 바인딩은 Cloudflare `@cf/baai/bge-m3`, `OpenAI-API-Compatible`였다. 이 구현은 해당 `OpenAI_APIEmbed` 어댑터를 포함한다. 실행 중인 서비스, 런타임 설정, 모델, 청킹, C128, prefix2400, Jina reranker v3.5는 이번 검증에서 변경하지 않았다. 실제 API 호출이나 원문 재인덱싱은 수행하지 않았다.

지원 대상은 **정확히 OpenAIEmbed와 OpenAI_APIEmbed 클래스**다. Azure, Astraflow 등 다른 하위 클래스와 다른 공급자는 기존 어댑터 배치 제한, 별도 제목 요청, 직렬 실행을 유지한다. LLMBundle의 기존 encode 래퍼를 그대로 호출하며 모델을 직접 호출하지 않는다. encode에는 DB 사용량 쓰기나 공용 `last_usage` 갱신이 없고, 요청마다 로컬 사용량을 반환한다. Langfuse를 사용하는 번들은 관측 클라이언트의 동시성 안전성을 이번 작업에서 입증하지 않았으므로 직렬 실행한다. 제목 통합과 배치 확대는 해당 번들에서도 사용 가능하다.

## 설정과 유효 동시성

환경변수는 향후 소스가 배포된 워커를 시작할 때 적용한다. 실제 `.env`나 실행 설정 파일은 수정하지 않았다.

| 설정 | 기본값 | 동작 |
| --- | --- | --- |
| `EMBEDDING_BATCH_SIZE` | 16 | 양의 정수. 대상 어댑터와 서비스의 유효 입력 수는 최대 64. 기존 전역 설정을 사용하는 다른 경로의 더 큰 값을 새 전역 상한으로 금지하지 않는다. |
| `EMBEDDING_BATCH_CONCURRENCY` | 1 | 문서별 작업자 수 1 또는 2. 지원 어댑터이며 Langfuse가 비활성인 경우에만 2 적용. |
| `MAX_CONCURRENT_EMBEDDING_REQUESTS` | 기존 `MAX_CONCURRENT_CHUNK_BUILDERS` 값, 없으면 1 | 같은 이벤트 루프의 모든 임베딩 작업이 공유하는 limiter 용량. 양의 정수. 파싱/청킹 용량과 독립적으로 조정 가능. |
| `EMBEDDING_REQUEST_TOKEN_LIMIT` | 0 | 대상 어댑터의 요청당 합계 토큰 추정 상한. 0은 미지정. 공식 OpenAI 엔드포인트는 이 설정과 별도로 300,000 상한 적용. |

유효 요청 동시성은 문서별 작업자 수, 공유 limiter 용량, 공급자 경로의 안전한 실행 범위 중 가장 작은 값이다. **문서별 설정만 2로 바꾸고 공유 limiter가 1이면 실제 요청은 한 번에 하나**다. 공유 용량을 2로 바꿔도 문서별 기본값이 1이면 한 문서 안의 배치는 직렬이다. 여러 워커 프로세스 간 전역 API 할당량은 이 이벤트 루프 limiter가 통제하지 않는다.

후속 실서비스 시험 후보는 배치 32, 문서별 동시성 2, 공유 용량 2다. 이 조합은 합성 검증을 통과했으며 Cloudflare의 현재 계정/엔드포인트 처리량과 할당량은 미검증이다. 기본값은 16/1로 유지한다. 동시 호출이 제한되거나 효과가 없으면 동시성을 1로 두고 검증된 배치 크기와 제목 통합으로 요청 수를 줄일 수 있다. 추가 자동 재시도는 만들지 않았다.

공식 [OpenAI embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)는 입력 배열 2,048개 이하와 요청 합계 300,000토큰 제한을 설명한다. 정확한 `api.openai.com` 호스트에 대해서만 합계 제한을 적용하며 어댑터의 64개 상한은 배열 제한보다 작다. 기존 8,191토큰 입력 truncation을 유지한다. 설정한 더 낮은 합계 상한도 지킨다.

[Cloudflare BGE-M3](https://developers.cloudflare.com/workers-ai/models/bge-m3/)와 임의 호환 엔드포인트에는 OpenAI의 공급자 한도를 자동 적용하지 않는다. 이들에는 기본 배치 16을 유지하며 운영자가 별도 검증한 범위만 명시적으로 선택한다. `EMBEDDING_REQUEST_TOKEN_LIMIT`과 새 로그의 입력 토큰 수는 기존 cl100k tokenizer의 로컬 추정이다. BGE-M3 등 다른 tokenizer의 실제 공급자 토큰 수와 일치한다고 가정하지 않으며 공급자 공식 한도 검증을 대신하지 않는다.

## 실패, 취소와 관측

고정된 최대 두 작업자가 배치를 순서대로 가져오고 결과는 원래 배치 인덱스에 저장한다. 진행률은 완료된 본문 수로 단조 증가한다. 모든 결과의 개수, 차원, 수치 유효성과 토큰 사용량을 확인한 뒤 벡터를 문서에 붙인다. 잘못된 결과나 배치 실패 시 부분 벡터를 붙이지 않는다. 응답 인덱스가 있으면 모든 입력에 대한 유일하고 완전한 인덱스를 요구하며 반환 순서를 복원한다. 인덱스가 전부 없는 호환 응답은 기존처럼 공급자의 반환 순서를 입력 순서로 사용한다.

동기 `encode`를 실행하는 `thread_pool_exec` 태스크는 shield 처리한다. 실패나 취소 후 새 **서비스 배치**를 시작하지 않고 이미 실행 중인 `encode` 전체가 끝난 뒤 limiter를 반환한다. 요청 토큰 상한 때문에 어댑터가 한 서비스 배치를 여러 요청으로 나눈 경우에는 취소 뒤에도 해당 `encode`의 남은 내부 요청이 순차 실행될 수 있다. 이 작업 범위는 이미 시작한 서비스 배치의 최대 64개 입력으로 제한된다. 반복 취소도 이벤트 루프를 차단하거나 limiter를 조기에 반환하지 않는다. Python은 실행 중인 SDK 스레드를 강제 중지할 수 없으므로 취소 완료까지 걸리는 시간은 남은 내부 요청, 공급자/SDK timeout과 내부 재시도에 좌우된다. 취소 이후 완료되는 내부 요청을 포함해 공급자가 처리한 요청에는 사용량과 과금이 발생할 수 있다.

DEBUG 로그는 배치 인덱스, 입력 수, 입력 토큰 추정, limiter 대기 초, encode 경과 초, 공급자가 보고한 사용 토큰을 기록한다. 서비스의 `submitted_tokens_estimate`는 LLMBundle 전처리 전 입력, 어댑터의 `input_tokens_estimate`는 어댑터 truncation 후 입력이다. encode 경과 시간에는 SDK 내부 재시도, 네트워크와 공급자 계산이 포함될 수 있어 모델 계산 시간으로 부르지 않는다. DEBUG가 비활성이고 요청 토큰 상한도 없으면 새 계측용 토큰 계산은 생략한다. 텍스트, 인증 URL, 키를 새 로그에 포함하지 않는다.

## 합성 측정

255개 생성 본문과 제목 하나, 요청당 고정 20ms 지연, 3회 반복의 중앙값이다. 인프로세스 공급자 stub 앞까지는 실제 EmbeddingService, LLMBundle, OpenAI_APIEmbed와 실제 numpy/asyncio/스레드를 사용했다. 공급자 응답을 역순으로 반환해 인덱스 정렬도 검증했다.

| 경로 | 실제 요청 수 | 최대 동시 요청 | 중앙값(초) |
| --- | ---: | ---: | ---: |
| 기존 제목 별도 + 본문16 | 17 | 1 | 0.359339 |
| 제목 통합16 | 16 | 1 | 0.338845 |
| 제목 통합32 | 8 | 1 | 0.174343 |
| 제목 통합64 | 4 | 1 | 0.087827 |
| 제목 통합32 / 문서2 / 공유1 | 8 | 1 | 0.173126 |
| 제목 통합32 / 문서2 / 공유2 | 8 | 2 | 0.088119 |
| 제목 통합64 / 문서2 / 공유2 | 4 | 2 | 0.046485 |

모든 경로에서 벡터는 원소별로 정확히 같고 사용량은 766토큰이다. 제목 통합의 요청 수는 `ceil((본문 수 + 1) / 배치 크기)`이므로 본문 수가 배치 크기의 배수인 경우 기존 대비 요청 수가 줄지 않을 수 있다. 긴 실제 청크, 네트워크, SDK 재시도, rate limit, 공급자 계산과 전체 인덱싱은 이 측정에 포함하지 않았다. 위 합성 시간을 운영 단축률로 해석하지 않는다.

## 재현과 검증

격리된 로컬 이미지 `docmind-ragflow-pytest:kordoc-pilot`에서 `--network none`, 앱 소스 읽기 전용 mount를 사용했다. Python 3.13.11, numpy 1.26.4, pytest 9.1.1, pytest-asyncio 1.4.0이다. 이미지에 없는 pytest-asyncio는 기존 `.local/kordoc-independent-root/deps`의 순수 Python 패키지를 읽기 전용으로 연결했다. 운영 컨테이너에 의존성을 설치하지 않았다.

저장소 루트의 PowerShell에서:

```powershell
docker run --rm --network none --entrypoint python `
  --mount 'type=bind,source=/mnt/c/DocMindDev/docmind/app,target=/workspace,readonly' `
  --mount 'type=bind,source=/mnt/c/DocMindDev/docmind/.local/kordoc-independent-root/deps,target=/test-deps,readonly' `
  -e PYTHONPATH=/workspace:/test-deps -e NLTK_DATA=/ragflow/nltk_data -w /workspace `
  docmind-ragflow-pytest:kordoc-pilot -m pytest -o cache_dir=/tmp/pytest-cache `
  test/unit_test/rag/llm/test_embedding_model.py `
  test/unit_test/rag/svr/task_executor_refactor/test_embedding_service.py `
  test/unit_test/rag/svr/task_executor_refactor/test_embedding_throughput.py `
  test/unit_test/common/test_embedding_throughput_settings.py -q

docker run --rm --network none --entrypoint python `
  --mount 'type=bind,source=/mnt/c/DocMindDev/docmind/app,target=/workspace,readonly' `
  -e PYTHONPATH=/workspace -e NLTK_DATA=/ragflow/nltk_data -w /workspace `
  docmind-ragflow-pytest:kordoc-pilot tools/benchmark_embedding_throughput.py
```

경로의 `/mnt/c`는 이 호스트의 Docker 명령이 WSL로 연결되는 구성 때문이다. 다른 환경은 자기 checkout을 `/workspace`에 연결한다. 일반 개발 환경에서는 app에서 동일 pytest 목록과 `PYTHONPATH=. python tools/benchmark_embedding_throughput.py`를 실행한다. 이미지의 선택적 패키지가 오프라인 캐시로 전환하는 import 경고는 계측 시간 밖이며 실제 API 호출은 없다.

최종 집중 회귀 검증은 85개 통과했다. 제목/본문 truncation, 가중 혼합과 기존 dtype별 반올림, 실제 어댑터 배치, 공급자 제한 분리, 응답 인덱스, 공유 limiter, 완료 순서, 스레드 실패/반복 취소, 부분 벡터 차단과 설정 오류를 검증했다. 전체 제품/통합 테스트나 실제 공급자 재인덱싱 검증 결과로 확대 해석하지 않는다.

새 서비스 처리량 테스트는 기존 공급자 테스트의 경량 패키지 bootstrap을 명시적으로 재사용한다. 실제 임베딩 모듈과 LLMBundle 코드는 그대로 로드하며 무관한 모델 자동 탐색만 생략하므로 다른 테스트 파일의 수집 순서에 의존하지 않는다. `test/unit_test/rag/svr/task_executor_refactor/test_embedding_throughput.py`만 지정해도 독립 실행할 수 있다.

## 되돌리기

후속 배포에서 동시성을 끄려면 `EMBEDDING_BATCH_CONCURRENCY=1`, 공유 용량을 기존 값으로, 배치 크기를 16으로 되돌리고 워커를 재시작한다. 이 설정 복원은 새 제목 통합을 유지한다. 이전 요청 구조까지 복원하려면 이 변경 커밋을 되돌린 소스를 배포한다. 이번 작업은 배포하지 않았으므로 현재 실행 서비스에 대한 rollback 작업은 없다.
