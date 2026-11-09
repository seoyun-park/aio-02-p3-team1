# 확정된 다섯 가지 CI 기준

CI/CD 구현 상태와 단계별 실패 처리 요약은 [배포 파이프라인 구현 결과](deployment-result.md)를 참고한다. 실제 EC2 배포는 아직 검증되지 않았다.

알림은 기존 Discord Webhook과 프론트/백엔드 담당자 ID를 유지한다. Slack은 추가하지 않았다. 성공/실패 알림과 지연·담당자 할당·에스컬레이션은 서로 다른 기능이며, 후자의 추가 구현은 이번 변경에 포함하지 않는다.

## 검사와 차단

- `Verified application pipeline`이 dev/main push와 PR에서 실행된다. 경로 필터로 전체 검사가 사라지지 않는다.
- 백엔드: 런타임 이미지 빌드 → 같은 런타임을 상속한 테스트 이미지 → 임시 PostgreSQL 18에 마이그레이션 → pytest. app 줄 커버리지 70%를 강제한다.
- 테스트 이미지의 dev 의존성 설치는 런타임 `pip freeze`를 constraints로 적용해 런타임 의존성 교체를 막는다.
- 프론트: 운영 이미지 빌드 → ESLint·TypeScript → 기존 Node 테스트와 DOM 상호작용 테스트. c8이 전체 실행 가능한 src를 측정하고 줄·문장·함수 70%, 분기 60%를 강제한다. 타입 전용 파일과 선언 파일만 제외한다.
- 임시 PostgreSQL은 tmpfs를 사용한다. `.env`/`.env.prod`를 읽지 않고 CI 전용 비밀번호를 사용한다. 기존 DB 테스트의 합성 입력·rollback, 모의 LLM·외부 API를 유지한다. DOM 테스트도 fetch 경계만 합성 응답으로 대체한다.
- 통합 작업은 같은 런타임 이미지를 내려받아 실제 Nginx→FastAPI→PostgreSQL 경로의 준비 상태, 로그인, 비인증 차단, 시간표 조회를 검사한다. 실제 외부 API/LLM은 호출하지 않는다. 실제 추천 전체 E2E를 검증한 것으로 확대 해석하지 않는다.
- `pipeline-gate`는 backend/frontend/integration 세 결과가 모두 success인 경우에만 통과한다. failure/cancelled/skipped/누락은 실패다. 일반적인 취소는 GitHub의 기본 의존성 처리에서도 후속 작업을 막는다.
- GitHub 브랜치 보호에서 새 `pipeline-gate`를 필수로 지정해야 PR 병합도 차단된다. 이 저장소 설정 변경은 아직 수행하지 않았다.

## 같은 이미지 게시·배포

1. 각 작업에서 성공한 런타임 이미지를 docker save로 보관한다.
2. 컨테이너 통합 테스트에 사용한 이미지 아카이브의 config 바이트를 SHA-256으로 계산해 `tested-images.json`에 기록한다. Docker 버전별 `docker inspect .Id` 의미 차이에 의존하지 않는다.
3. 신뢰된 dev push만 이미지 게시 가능하다. 게시 작업은 아카이브의 태그·config 해시를 대조한 후 docker load한다. 재빌드하지 않는다.
4. 게시 후 ECR manifest의 config digest가 테스트 이미지 ID와 같은지 다시 확인한다. backend/frontend의 registry digest를 `release.json`에 기록하고 `verified-release` Artifact로 보관한다.
5. 배포 준비 도구는 성공 완료된 해당 pipeline의 dev push 실행인지 GitHub API로 확인하고, 그 실행의 verified-release Artifact만 다운로드한다. 로컬 소스 SHA도 일치해야 한다. 실패·취소·진행 중 실행이나 임의 수동 JSON은 배포 준비에 사용할 수 없다.
6. `python scripts/ci/prepare_deployment.py --run-id <성공한 실행 ID>`로 `.env.verified`를 생성한다. GitHub CLI의 Actions 읽기 인증이 필요하다. 파일에는 비밀번호가 아닌 두 이미지의 digest 참조만 기록한다.
7. 기존 운영 절차의 Compose 명령에 `--env-file .env.prod --env-file .env.verified`를 이 순서로 함께 전달해 검증된 이미지 참조가 우선하도록 한다. 예: `docker compose --env-file .env.prod --env-file .env.verified -f compose.prod.yaml config --quiet`.

배포 준비 도구는 실제 EC2 배포·DB 마이그레이션을 실행하지 않는다. 새 CD 자동화 및 수동 관리자 권한으로 검증 절차를 우회하는 것까지 차단하는 운영 권한 정책은 별도 작업이다. 관리자 저장소의 별도 이미지 게시 workflow는 이 앱 두 이미지의 검증 계약과 구분한다.

## 증빙과 검증 상태

검사 결과/coverage/컨테이너 로그는 30일, 이미지 아카이브는 7일 보관한다. 로그 수집 후 이 작업의 Compose 스택만 정리한다. 정상·실패 시 cleanup이 실행된다.

이 문서는 구현 방식이다. GitHub Actions·ECR 게시·실제 EC2 동작 성공을 의미하지 않는다. 로컬 확인 결과는 작업 완료 메시지에 별도 기재하며, 컨테이너 시험은 Docker Desktop 엔진 준비 후 수행한다.

2026-11-07 로컬 Docker Linux 검증: 백엔드 67개 통과·줄 커버리지 70.83%, Node 22 프론트 36개 통과·줄/문장 88.14%·함수 89.57%·분기 77.21%. 양쪽 운영 이미지 빌드와 실제 Nginx→FastAPI 인증/시간표 smoke가 통과했다. 이미지 아카이브 config 해시 추출과 배포 계약 단위 테스트도 확인했다. 이는 수정 중인 로컬 작업 트리의 결과이며 GitHub 실행 실적이나 ECR/EC2 검증 결과가 아니다.
