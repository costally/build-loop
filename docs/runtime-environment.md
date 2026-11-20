# 런타임 환경 변수

배포용 .env 파일을 만들거나 전역 설정을 변경하지 않는다.

| 이름 | 출처와 적용 |
|---|---|
| BUILD_LOOP_EXPERIMENT_ENABLED | build-loop.cmd의 현재 프로세스에서만 1. 직접 Python 호출에서는 기본 비활성 |
| CODEX_HOME | 기존 Codex 호스트 환경. 미설정이면 사용자 홈의 .codex. 실제 sessions 기록의 허용 루트 |
| APPDATA | Windows 표준 환경. 검증용 CLI 실행 파일의 npm 기본 경로 탐색 |

이 세 키는 새 인증정보나 수동 .env 배포 요구가 아니다. 프로젝트 요청으로 호스트 환경을 바꾸지 않는다. Git 푸시만으로 전역 환경을 설정하지 않는다.
