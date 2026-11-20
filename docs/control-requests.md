# Codex 제어 요청 형식

이 문서는 에이전트용이다. 사용자는 아이디어와 질문에 대한 답만 말한다. 요청 형식을 알아내기 위해 본체 소스를 조사하거나 추측해서 반복 실행하지 않는다.

프로젝트의 `.build-loop/requests/<영문이름>.json`을 apply_patch로 작성하고 AGENTS.md의 제어 명령에서 `--request` 파일명만 바꿔 실행한다. 명령 앞에 환경 변수 설정이나 다른 셸 명령을 붙이지 않는다. 제어기가 쓰기 환경을 자체 관리한다. 작업 폴더는 다운로드 루트 그대로 유지한다.

`revision`은 직전 guide의 값이다. `thread`/`session`은 현재 실제 Codex 세션 ID, `session_file`은 호스트의 실제 sessions JSONL 절대 경로, `call`은 실제 request_user_input 호출 ID다. 식별값과 응답을 꾸미지 않는다.

## 첫 질문

```json
{"action":"question","revision":1,"question":{"id":"primary_use_case","header":"사용 상황","question":"초간단 메모장은 어떤 상황에서 쓰면 될까요?","options":[{"label":"개인 메모 한 장 (추천)","description":"열고 바로 적고 다시 확인하는 최소 흐름입니다."},{"label":"여러 메모 관리","description":"메모를 여러 개 만들어 목록에서 고릅니다."}]}}
```

`question`은 메뉴 질문 하나다. `id`, `question`, `options`는 필수이고 `header`는 선택이며 12자 이하다. 실제 메뉴 호출에는 header를 포함한다. 이전 형식은 `question:{questions:[질문 하나]}`도 허용하지만, 저장할 때는 질문 하나로 정규화한다. `text` 같은 미지원 필드를 추가하지 않는다.

등록 성공 응답의 `question`을 **같은 턴에서 request_user_input({questions:[question]})로 표시**한다. 이전 기록에 header가 없을 때만 표시용 header를 추가한다. JSON 출력 자체는 메뉴가 아니다. 실제 응답을 받은 후 아래 selection 요청으로 연결한다.

## 요청별 필수 인수

모든 상태 변경 요청은 현재 `revision`을 포함한다.

| action | 추가 필드 |
|---|---|
| guide | 없음 (조회, revision 불필요) |
| read | path: 프로젝트 내부 상대 파일 경로 |
| list | path: 프로젝트 내부 상대 폴더 경로, 생략 시 프로젝트 루트 |
| search | query: 검색할 문자열 |
| question | question: 위 질문 객체 하나 |
| selection | session_file, session, call, question: 질문 ID |
| stage-review | stage: brief/prd/policy/design/qa, thread |
| stage-selection | stage, session_file, session, call |
| spec-review | version: 검토 버전, thread |
| spec-selection | session_file, session, call |
| plan | tasks: REQ/TC·완료 조건·검증 명령을 연결한 작업 배열 |
| check | task: 등록된 작업 ID |
| demo / completion | thread |
| demo-selection / completion-selection | session_file, session, call |
| resume-selection | session_file, session, call |
| external-block | reason: 실제 외부 권한·계정·도구 차단 근거 |
| recovery-review | thread, reason: 현재 구현 작업의 실패 한도·미확인 실행 재시도 근거 |
| recovery-selection | session_file, session, call |
| change-review | cr: CR 파일 경로, files: 변경 문서의 상대 경로→전체 내용 객체, version, thread |
| change-apply | session_file, session, call |

## 형식 오류와 중단

plan의 tasks 항목은 정확히 `id`, `req`, `tc`, `title`, `acceptance`, `check`, `environment` 필드를 가진다. 예: `{"id":"TASK-001","req":"REQ-001","tc":"TC-001","title":"메모 입력","acceptance":"합의한 입력·저장 조건 통과","check":["python","verify.py"],"environment":"Windows 로컬"}`. req/tc는 실제 승인 문서에 존재하는 ID, check는 실제 실행 가능한 argv 배열이어야 한다. 모든 REQ를 연결하며 같은 작업 ID를 중복하지 않는다. 예제 명령을 구현 없이 검증 증거로 사용하지 않는다.

질문 등록 같은 제어 요청의 검증 오류는 구현 검증 실행 실패가 아니다. 잘못된 요청은 상태와 revision을 바꾸지 않는다. 응답의 `request_help`와 `example`을 보고 잘못된 필드를 고친다. 같은 요청을 그대로 반복하지 않는다. 구현 작업이 없는 기획 단계에서 recovery-review를 호출하지 않는다.

이미 paused라면 guide의 재개 question을 실제 메뉴로 먼저 표시한다. 실제 사용자 확인을 resume-selection으로 연결한 뒤, 새 guide의 revision으로 질문을 등록한다. 상태를 직접 수정하거나 자동 승인하지 않는다.
