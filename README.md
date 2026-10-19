# DOCMIND — CPU 서버 설치

현재 배포된 DOCMIND를 기준으로 정리한 제품 소스입니다. **GPU 없이 CPU로 인덱싱하며 OCR은 사용하지 않습니다.** 기존 문서·청크·벡터 DB·대화 기록은 가져오지 않습니다. 배포팀은 서버별 경로를 설정한 뒤 설치 스크립트를 실행합니다. 기존 API 키와 서버 인증 키는 별도의 암호화 설정에서 이어받습니다.

## 1. 설치 환경

현재와 같은 **Windows x64 + WSL2 Ubuntu + Linux Docker Engine**을 기준으로 합니다.

- PowerShell 7(`pwsh`), Git, Python 3.11 이상
- 설치 계정에서 실행 가능한 WSL2 Ubuntu, Docker Compose v2(`up --wait` 지원), Docker Buildx 플러그인(BuildKit 빌드). WSL 안에서 `docker compose version`, `docker buildx version`이 모두 성공해야 합니다. 설치기가 두 가지를 먼저 검사합니다.
- 용량 계획 시작점: Windows/WSL 전체 RAM 24 GB 이상, 여유 디스크 80 GB 이상. 문서/색인 양에 따라 증설합니다. 최초 웹 빌드는 힙 8 GB를 사용합니다.
- 최초 빌드 시 이미지 저장소, Ubuntu/NGINX/Node 패키지 저장소, PyPI, GitHub, Hugging Face, PyTorch CPU wheel 저장소에 접근할 수 있어야 합니다.
- 승인된 `uEncryptor2.exe`와 인증서·라이선스·복호화 환경을 새 서버에 설치해야 합니다. exe는 Git에 포함되지 않습니다.
- 설치/작업 계정이 암호화 원본을 읽고 임시 평문 경로에 쓸 수 있어야 합니다. 임시 경로와 `-preview` 경로를 백업에서 제외합니다. 기본 설정은 해당 볼륨의 BitLocker 보호도 요구합니다.
- 새 서버에서 기존 답변 모델 API와 Jina reranker API에 접근할 수 있어야 합니다. CPU 전환 대상은 로컬 임베딩이며 답변 모델은 기존 외부 API입니다.

기본 Docker 실행 방식은 WSL입니다. Docker Desktop Linux engine은 `docker_mode: "desktop"`으로 선택할 수 있습니다. Windows containers 모드는 지원하지 않습니다.

## 2. 소스와 설정 받기

PowerShell 7에서 로컬 Windows 드라이브에 설치합니다. 비공개 저장소 접근 권한이 필요합니다.

```powershell
git clone --depth 1 --single-branch --branch main https://github.com/Dongmin103/docmind-private.git C:\DocMind
Set-Location C:\DocMind
python -m pip install cryptography
New-Item -ItemType Directory -Force .local | Out-Null
Copy-Item deployment/cpu/server.example.json .local/server.json
```

[암호화 설정 릴리스](https://github.com/Dongmin103/docmind-private/releases/tag/deployed-baseline-20261019)에서 `docmind-deployed-20261019-settings.tar.enc`를 받습니다. SHA256:

```text
01625db5b2ea5ea0808e9db77fa5f22580e394ffe4d0675c41f2c1e69e9b069b
```

32바이트 `recovery.key`는 관리자가 별도 전달합니다. 키는 저장소/릴리스에 올리지 않습니다. 두 파일을 예를 들어 `C:\DocMindSecrets`에 보관하고 설치 계정과 SYSTEM만 접근하도록 제한합니다.

```powershell
Get-FileHash C:\DocMindSecrets\docmind-deployed-20261019-settings.tar.enc -Algorithm SHA256
```

설치기는 메모리에서 설정을 복호화하고 내부 파일 해시를 검사합니다. 기존 DB·벡터·객체 데이터는 복원하지 않습니다. 과거 경로도 새 서버 입력으로 대체합니다.

## 3. 새 서버 경로 설정

`.local/server.json`을 편집합니다. JSON 경로에는 `/` 또는 `\\`를 사용합니다.

| 항목 | 설정 내용 |
| --- | --- |
| `project_name` | 고유 Docker 프로젝트명. 설치 후 유지. |
| `wsl_distribution` | `wsl -l -v`에 표시되는 이름. 기본 `Ubuntu`. |
| `decrypt_executable` | 새 서버 `uEncryptor2.exe`의 절대 경로. 승인된 서명과 실제 파일 해시를 검사. |
| `plaintext_root` | 원본 폴더와 겹치지 않는 임시 평문 절대 경로. 예: `D:/DocMindWork/plaintext`. |
| `require_bitlocker` | 기본 `true`. 조직의 디스크 보호 정책에 맞춤. |
| `backup_exclusion_acknowledged` | 평문 루트와 `-preview` 경로의 백업 제외를 구성한 뒤 `true`. |
| `workspace_email` | 새로 생성할 로그인 이메일. 기존 계정 DB는 이전하지 않음. |
| `sources[].source_id` | 각 문서 루트의 고유 식별자. 예: `dept-main`. 설치 후 유지. |
| `sources[].display_name` | 화면에 표시할 소스 이름. |
| `sources[].root` | 설치 계정이 읽을 수 있는 암호화 원본의 Windows 절대 경로 또는 UNC 경로. |
| `answer_base_url` | 비워두면 기존 답변 API 주소 유지. 접근할 수 없다면 사용 가능한 동일 API 주소 지정. |
| `cpu_threads` | 기본 `2`. CPU 임베딩 스레드와 컨테이너 CPU 한도. |
| `web_port`, `api_port` | 기본 `18080`, `19380`. 서로 다른 미사용 포트. |

**문서 루트는 자동으로 찾지 않습니다.** 여러 폴더면 `sources` 항목을 추가합니다. 파일 서버의 `D:/...`는 DOCMIND 서버의 D 드라이브가 아닙니다. 공유 폴더는 설치 계정에서 접근되는 `//fileserver/share/Department` 같은 UNC 경로를 지정합니다. 로그인 세션마다 달라지는 매핑 드라이브보다 UNC를 권장합니다.

제공된 VDI `login.do`/`folders.do` REST API로 사용자별 폴더를 자동 등록하는 기능은 이번 설치기에 포함되지 않습니다. 현재는 명시적으로 등록한 소스와 공용 작업공간을 사용합니다. VDI 로그인·사용자별 ACL이 필요한 배포는 별도 연동이 필요합니다.

## 4. 설치

설치 계정의 관리자 PowerShell 7에서 실행합니다. 작업 스케줄러도 같은 계정으로 등록합니다.

```powershell
pwsh -NoProfile -File .\tools\deployment\Install-DocMindCpuServer.ps1 `
  -ServerConfig .\.local\server.json `
  -SettingsArchive C:\DocMindSecrets\docmind-deployed-20261019-settings.tar.enc `
  -RecoveryKey C:\DocMindSecrets\recovery.key `
  -StartWorkers
```

실행 순서:

1. 경로/입력 검사, `.local/cpu-server` 생성 및 접근 권한 제한
2. DOCMIND 웹/API, CPU GTE, Kordoc, 미리보기 이미지 빌드
3. 고정 버전 GTE 모델 다운로드(가중치는 Git에 포함하지 않음)
4. 새 MySQL·Elasticsearch·Redis·MinIO 볼륨과 서비스 시작 및 준비 상태 대기
5. 새 로그인 계정·768차원 CPU 임베딩·문서 소스 초기화
6. `-StartWorkers` 지정 시 수집·변경 감시·미리보기 Windows 작업 등록 및 실행

접속: `http://127.0.0.1:18080`. 이메일: `workspace_email`. 초기 비밀번호: **서버의 `.local/cpu-server/admin-password.txt`**. 비밀번호는 콘솔에 출력하지 않습니다. `.local/cpu-server`의 비밀 설정을 Git에 추가하거나 공유하지 않습니다.

빈 색인에서 새로 인덱싱합니다. CPU는 GPU보다 느릴 수 있으며 기본 문서 동시성과 임베딩 배치는 1입니다. OCR 요청은 서버에서 차단합니다. 텍스트가 없는 스캔 PDF의 이미지는 인식하지 않으므로 검색 내용이 생성되지 않습니다.

Windows 수집기는 같은 호스트의 loopback API를 사용합니다. Windows에서 복호화하고 임시 평문을 정리하며 Linux 컨테이너에서 Windows exe를 실행하지 않습니다.

## 5. 확인과 운영

```powershell
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Status

# CPU 모델 상태: device가 cpu인지 확인
wsl -d Ubuntu --exec /usr/bin/docker compose -f /mnt/c/DocMind/.local/cpu-server/compose.json exec -T gte python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18882/health').read().decode())"

Get-ScheduledTask | Where-Object TaskName -Like 'DocMind CPU docmind-cpu (*)' | Select-Object TaskName, State
```

WSL 이름/설치 경로/프로젝트명을 바꾸었다면 확인 명령도 맞춥니다. 비밀값이 들어 있는 `compose.json` 전체를 콘솔/지원 게시물에 출력하지 않습니다. 웹에서 새 폴더와 문서 유입, 텍스트 PDF 검색, 근거 있는 답변, 원문 미리보기 및 임시 파일 정리를 확인합니다. 작은 텍스트 PDF로 먼저 확인합니다.

외부 접속은 배포팀 TLS 리버스 프록시에서 `127.0.0.1:18080`으로 연결합니다. 기본 설치는 loopback에만 웹/API를 노출하며 내부 DB/파서는 호스트에 공개하지 않습니다.

작업 스케줄러는 **현재 계정 로그인 시** 실행하며 설치 시 즉시 시작할 수 있습니다. Windows 로그인 전/로그아웃 후 무인 운영은 계정·WSL·복호화 프로그램 실행 조건의 별도 검증이 필요합니다. 컨테이너는 Docker Engine이 시작되면 재시작 정책을 따릅니다.

자동 등록 전에 수동으로 확인하려면 설치 명령에서 `-StartWorkers`를 빼고 각각 별도 PowerShell 창에서 실행합니다.

```powershell
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Watch
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Ingest
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Preview

# 확인 후 자동 실행 등록. 기존 작업의 중복 등록은 거부함.
pwsh -File .\tools\deployment\Register-DocMindCpuWorkers.ps1 -StartNow
```

### 설치 실패 후 재개

설정 생성 이후 설치기를 다시 실행하면 기존 키를 덮어쓰지 않도록 중단합니다. 원인을 해결한 뒤 필요한 단계부터 재개합니다.

```powershell
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Build
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action DownloadModels
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Up
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Bootstrap
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Status
```

`Bootstrap`은 같은 계정/모델/소스에 재실행할 수 있습니다. 다른 사용자가 있거나 모델 설정이 충돌하는 DB는 거부합니다. 새 설치용이며 기존 서비스 업그레이드 도구가 아닙니다.

정지 시 작업 스케줄러의 `DocMind CPU <project_name> (...)` 작업을 먼저 정지하고 진행 중 임시 평문 정리를 확인한 뒤 실행합니다. `Stop`은 볼륨을 삭제하지 않습니다.

```powershell
pwsh -File .\tools\deployment\Start-DocMindCpuServer.ps1 -Action Stop
```

새 DB/색인/객체 볼륨과 별도 암호화 설정의 백업은 배포팀에서 구성합니다. 임시 평문/미리보기 tmpfs는 백업하지 않습니다. 설치 디렉터리를 옮기면 생성 설정의 절대 경로도 검토해야 합니다. 경로 변경 시 `worker.json`, `preview-worker.json`, `server.json`, compose bind 경로를 맞춥니다.

## 제품 구성과 검증 범위

- `app/`: DOCMIND 웹/API, 필요한 검색·문서 처리 코드와 빌드 입력
- `tools/local_embedding/`: GTE CPU 서비스 및 모델 다운로드
- `tools/windows/`: Windows 수집·동기화·복호화 보조 코드
- `tools/deployment/`: 설치·초기화·시작·제품 패키지 도구
- `deployment/cpu/server.example.json`: 새 서버 설정 예제

범용 RAGFlow SDK·Helm·예제·다국어 문서·개발 스냅샷은 제외합니다. DOCMIND가 사용하는 내부 런타임과 Apache 라이선스/제3자 고지는 유지합니다. 내부 모듈명/의존 이미지 이름에 `ragflow`가 남을 수 있습니다.

CPU 서비스 단위 테스트 21개, OCR 비활성화 회귀 테스트 219개, 실제 SQLite 초기화 테스트 8개, 설정 생성 테스트 7개와 제품 패키지 검사 7개를 통과했습니다. CPU 전용 Linux 이미지의 실제 빌드와 HTTP 임베딩도 확인했습니다(torch 2.8.0+cpu, CUDA 사용 불가, 한글/영문 768차원 정규화 벡터, 인증 누락 401). 실제 암호화 설정 복원·Compose 구문·비밀값의 소스 미포함도 확인했습니다. 별도 깨끗한 프런트엔드 의존성 설치 후 Vite production build도 통과했습니다. 기존 전체 TypeScript 검사 오류는 남아 있습니다. 대상 서버 접근 및 전체 신규 빌드부터 실문서 인덱싱까지의 현장 인수 시험은 수행하지 않았으므로 배포팀이 위 확인 절차로 마무리해야 합니다.

제품 소스 ZIP 생성/무결성 확인:

```powershell
python tools/deployment/build_product_package.py --output .local/docmind-product.zip --format zip
python tools/deployment/build_product_package.py --verify .local/docmind-product.zip
```

배포팀 전달물: 이 저장소 또는 제품 ZIP, 암호화 설정 파일, 별도 전달 복호화 키, 새 서버 경로 설정 및 해당 계정의 문서/복호화 권한.
