# 비공개 배포본과 기존 설정 가져오기

소스는 `Dongmin103/docmind-private`의 `main`, 현재 키·실행 설정은 같은 비공개 저장소의 `deployed-baseline-20261019` 릴리스에 있다. 공개 저장소에는 이번 변경이나 키를 올리지 않는다.

## 기존 API 키 보존

릴리스 첨부파일 `docmind-deployed-20261019-settings.tar.enc`는 기존 키를 바꾸지 않고 보존한 AES-256-GCM 암호화 파일이다. 인증된 기존 `restore_backup.py` 형식과 같다. 복호화 키 `recovery.key`는 원래 PC의 별도 제한된 폴더에서 전달받는다. 키를 저장소나 릴리스에 올리지 않는다.

```powershell
python -m pip install cryptography
python restore_backup.py .\docmind-deployed-20261019-settings.tar.enc --key C:\SecureTransfer\recovery.key --output C:\SecureTransfer\settings-private.tar
```

복호화 결과에는 실제 자격증명이 포함된다. 별도 접근 제한 폴더에서 추출한다. 암호화 파일의 크기·SHA-256·검증 결과는 `DEPLOYMENT-SETTINGS-2026-10-19.json`에 있다. 내부 `manifest.json`으로 파일별 해시를 확인할 수 있다.

포함 범위:

- 실제 API Compose 및 컨테이너 실행 설정, 서버 설정과 인증용 키 파일
- Jina·호스트 작업자 공유 키, GTE API 키 및 실행 경로 설정
- 모델 공급자·인스턴스·모델 연결과 시스템 설정의 구성 레코드
- 색인·미리보기·감시기의 실제 설정 파일
- 현재 API 컨테이너의 컴파일된 웹 자산

원본 문서, 사용자·대화·검색 색인 데이터, 모델 가중치, Docker 이미지와 상용 복호화 프로그램은 포함하지 않는다. 구성 레코드의 tenant/provider ID는 기존 환경의 값이다. 새 DB에 그대로 자동 삽입하지 말고 새 환경의 로그인·워크스페이스와 연결해야 한다. 기존 데이터까지 이전하려면 별도 DB·색인 이전 작업이 필요하다.

## 다음 서버에 맞출 작업

다음 배포 대상에는 GPU가 없고 OCR을 사용하지 않는다는 사용자 조건을 기록한다. 현재 기준본은 실행 중인 버전 그대로이므로 다음 작업이 남아 있다.

1. GTE 서비스에 CPU 실행 장치 선택과 CPU용 처리량 설정을 추가한다.
2. GPU OCR 서비스의 자동 시작과 OCR 요청 기능을 제외한다.
3. 기존 역할별 launcher/config의 절대 경로·원본 접근 경로·API 주소를 새 서버 값으로 연결한다. 기존 API 키 값은 재사용할 수 있지만 IP 허용 목록과 외부 모델 서버 접근은 확인한다.
4. 제공받은 VDI `login.do` / `folders.do` API를 로그인·사용자 검색 범위에 연결한다. 이 기준본에는 해당 연동이 아직 없다.
5. 새 서버에서 문서 하나의 수집·정리·검색·답변까지 검증한다.

서버에 접근 가능한 경로와 파일서버의 실제 경로는 다를 수 있다. 반환 경로를 수집기의 승인된 source와 매핑하고 원본을 읽을 수 있는 Windows 실행부를 연결한다. 실제 파일 전송·복호화는 VDI 로그인 API가 제공하지 않는다.
