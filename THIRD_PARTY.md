# 참고 설계

build-loop의 인터뷰·기획 단계는 [PlanLoop](https://github.com/leoyun2010/planloop), 개발 하네스는 [claude-harness](https://github.com/leoyun2010/claude-harness)를 설계 참고로 삼았다. 지원 에이전트는 Codex다.

이 배포본의 Python 구현·기획 템플릿은 build-loop에서 작성했다. 참고 저장소의 전체 스킬이나 코드, 로컬 사용자 프로젝트를 번들하지 않는다. 런타임 외부 Python 패키지 의존성은 없다. Python·Git·Codex는 각각의 배포·사용 조건을 따른다.
