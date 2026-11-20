# build-loop

아이디어를 인터뷰로 구체화하고, 승인된 스펙을 따라 Codex가 구현·검증·시연·사용자 완료 승인까지 진행하도록 돕는 로컬 도구입니다.

**v0.1.0 개발 버전입니다.** Python 제어기와 Windows 실행·설치 경로를 제공합니다. GitHub 게시가 승인 스펙 전체의 완료나 실제 두 호스트 검증 완료를 뜻하지 않습니다.

## 시작

Windows에서 Python 3.13 이상, Git, Codex가 필요합니다. 추가 Python 패키지는 없습니다.

```powershell
git clone https://github.com/costally/build-loop.git
cd build-loop
.\build-loop.cmd --help
.\build-loop.cmd start --name my-project --idea "만들고 싶은 것을 적으세요"
```

기본 루트 `C:\build-loop`가 없으면 먼저 폴더를 만드세요. 생성된 프로젝트를 Codex 작업 폴더로 열고 `시작`이라고 요청하세요. 프로젝트의 AGENTS.md와 저장된 guide가 첫 미완료 작업을 안내합니다. 기존 PowerShell의 codex 함수나 전역 Codex 설정은 바꾸지 않습니다.

원하는 경우 본체를 기본 위치에 설치합니다.

```powershell
.\install.ps1
C:\build-loop\system\build-loop.cmd projects
```

설치는 기존 system 폴더를 덮어쓰지 않습니다. 별도 설치는 `install.ps1 -Root <다른경로>`를 사용합니다. 사용자 프로젝트는 본체와 분리합니다.

```text
C:/build-loop/
  system/                 도구 소스·템플릿·실행 명령
  projects/<name>/
    docs/                 브리프·PRD·정책·화면/동작·QA
    code/                 실제 결과물
    .build-loop/          로컬 SQLite·실행 기록, Git 제외
```

## 진행 순서

아이디어 → 실제 사례 인터뷰 → 브리프 → PRD → 정책 → 화면/동작 명세 → QA → 스펙 세트 승인 → REQ별 구현·검증·수정 → 실제 실행·시연 → 별도 사용자 완료 승인.

- 한 번에 한 질문을 실제 선택 도구로 표시합니다. 추천·이유를 포함하며 빈 응답은 확정하지 않습니다.
- 기존 확정 답과 진행 상태를 보존합니다. 재개 시 파일·스펙·코드 해시와 revision을 대조합니다.
- 문서 존재만으로 승인하지 않습니다. 단계 확인과 스펙 세트 승인, 최종 완료 승인은 서로 다릅니다.
- REQ·TC·완료 조건·검증 명령·환경을 작업에 연결합니다. 현재 버전에 맞는 통과 증거와 시연 확인이 없으면 완료 처리하지 않습니다.
- 승인 요구 변경은 CR로 관리합니다. 범위·주요 설계·완료 기준을 임의 변경하지 않습니다.

## 구현 상태와 한계

구현: 프로젝트 생성/목록/재개 안내, SQLite 트랜잭션·동시 쓰기 검사, 인터뷰 답 저장, 로컬 동기 선택 관찰, 단계 확인·사용자 메시지 기반 승인 바인딩, 구조 검사, 개발 작업·검증 프로세스·시연/완료 확인, 동일 오류 복구 횟수 보존.

아직 검증·완성이 필요한 부분: 두 Codex 호스트에서의 전체 실제 사용자 흐름, 사용자 친화적인 승인 메뉴, 작업별 실제 훅 강제 연결, 비화면 명세 검사, 승인 변경의 전체 자동 반영, 성능 기준 전체 실증. `guard`는 개발 중인 실험 모듈이며 설치기가 자동 활성화하지 않습니다. 모든 작업을 강제로 통제하는 보안 경계로 설명하지 않습니다. Codex가 구현을 수행하며 이 프로그램 자체가 모델 작업을 자동 생성하지 않습니다.

현재 기술용 확인 경로는 대상·버전에 묶인 확인 문자열을 사용합니다. 사용자의 이전 승인이나 응답 없는 추천을 대신 승인으로 만들지 않습니다. 승인된 build-loop 제품 스펙은 `specs/`에서 확인할 수 있습니다.

## 검사

```powershell
python -W error::ResourceWarning tools/run_checks.py --output checks.json
```

로컬 통합 검사는 실제 Python 하위 프로세스를 실행하며 사용자 전달부는 모의합니다. 실제 제품 QA와 구분합니다. GitHub Actions에서도 같은 검사를 실행합니다.

## 명령

`start`, `projects`, `guide`, `workflow`, `stage-review`, `stage-confirm`, `check-spec`, `delivery`, `state`, `doctor`, `observe-selection`을 제공합니다. 각 명령의 `--help`로 인수를 확인하세요. `build-loop.cmd`는 해당 프로세스에서만 실험 쓰기를 활성화합니다. 직접 Python 모듈을 실행할 때는 쓰기가 기본 비활성입니다.

사용자 DB·대화·계정 정보는 GitHub에 전송하지 않습니다. 도구 코드의 라이선스는 MIT입니다. 설계 참고는 [THIRD_PARTY.md](THIRD_PARTY.md)에 기재했습니다.
