# EC2 운영 배포 절차

2026-11-07 read-only 인벤토리에서 확인한 현재 대상은 기존 서비스가 운영 중인 dev-stud-server다. 앱·관리자·PostgreSQL 컨테이너, `seoul-wave-prod-postgres-data` 및 `seoul-wave-prod-monitor-state` 볼륨, `.env.prod`, 두 Compose 파일과 호스트 Nginx가 존재한다. `.env.prod` 내용과 비밀값은 읽거나 기록하지 않았다. 2026-11-06의 “컨테이너·볼륨 없음” 관찰과 공개 IP/DNS는 현재 상태로 간주하지 않는다.

## 자동 배포 경로

`dev`에 대한 `Verified application pipeline`의 성공한 push 완료만 CD 대상으로 삼는다. CD는 원본 workflow run의 `verified-release` 하나만 조회하고, 만료되지 않은지와 GitHub artifact SHA-256을 검사한 뒤 `release.json`을 기존 `validate_release` 및 배포 계약으로 검증한다. GitHub API bearer token은 첫 API 요청에만 보내며 artifact의 교차 호스트 redirect에는 전달하지 않는다. 해당 커밋이 계속 최신 `dev` HEAD이고 한국 시간 09:00–18:00 접수 구간인지 SSM 호출 직전에도 다시 확인한다.

- 동시성 그룹은 하나로 고정하고 진행 중인 배포는 취소하지 않는다 (`cancel-in-progress: false`).
- OIDC 역할은 `arn:aws:iam::909345918351:role/SeoulWave-Deploy`, 리전은 `ap-northeast-2`다. SSM 문서 `SeoulWave-Deploy-v1` 버전 `1`과 인스턴스 `i-0bedbcc72c616cdb1`만 사용한다. 파라미터는 원본 SHA/run ID, 두 digest 이미지, 두 config ID뿐이다. SSH 키, 임의 명령, 다른 문서나 인스턴스는 사용하지 않는다.
- 디스패처는 다단계 SSM 문서의 `runFixedDeployment` step을 지정해 polling한다. 해당 step이 아직 materialize되지 않아 발생한 `InvalidPluginName`은 초기 응답으로 재시도하며, 선행 고정 step의 실패도 확인한다. 고정 step들이 아직 조회되지 않는 `InvalidPluginName`/`InvocationDoesNotExist` 상태가 120초간 지속되면, plugin 오류는 `ssm_plugin_not_materialized`, 기타 invocation 미생성은 `ssm_poll_timeout`으로 빠르게 종료해 배포 잠금을 오래 점유하지 않는다. 선행 step이 실제 `InProgress`이면 일반 SSM 실행 제한 안에서 계속 기다린다. 성공 결과는 release ID, backend/frontend의 이미지 identity, migration dry-run, loopback HTTP 점검(프록시 health, 보호 API, 로그인, 시간표)이 모두 기대값과 일치해야 한다. Docker image store에 따라 실행 이미지 ID가 config digest 또는 해당 ECR manifest digest로 표시될 수 있으므로, 둘 중 현재 릴리스에서 직접 도출한 값만 허용한다. 두 이미지를 pull한 뒤 Compose 변경 전에 같은 identity를 사전 확인하고, 서비스별 불일치는 안전한 코드로 구분한다. `deployment-evidence` Artifact는 원본 run/SHA, release Artifact ID/digest, 배포 대상 이미지 digest, 확인된 image identity와 HTTP 점검, SSM command ID/최종 상태를 보존한다. 원격 stdout/stderr와 비밀값은 증빙이나 Discord 알림에 넣지 않는다.
- 기존 `DISCORD_WEBHOOK_URL` convention을 사용해 배포 성공/실패만 알린다. 알림에는 비밀값·원격 stdout/stderr를 넣지 않고 사용자/소유자 mention도 하지 않는다. 15분 무응답 owner escalation은 구현 범위 밖이며 별도 운영 항목으로 남아 있다.
- 자동 경로는 backend, frontend, itinerary-worker, database-health-monitor 앱 서비스만 다룬다. DB migration, 데이터 적재, PostgreSQL·관리자 서비스 변경, `.env.prod` 변경은 하지 않는다. 새 이미지 검증 실패 시 호스트 runner의 `not_applied`, `rolled_back`, `rollback_failed` 결과를 구분해 확인한다.

## 현재 호스트 경계

- 호스트 Nginx :80 → 사용자 앱 loopback 127.0.0.1:8080, `/admin/` → 관리자 loopback 127.0.0.1:8081. 컨테이너 앱 포트는 loopback에만 공개된다.
- PostgreSQL 18과 기존 앱/관리자 서비스는 이미 존재한다. `seoul-wave-prod-postgres-data`와 `seoul-wave-prod-monitor-state` named volume, 운영 `.env.prod`, 호스트 Nginx 설정을 보존한다.
- 배포 대상 ECR은 backend `seoulwave` 및 frontend `seoul-wave-frontend`의 digest-pinned 참조다. CI가 검증한 이미지를 그대로 사용하며 배포 단계에서 rebuild하지 않는다. `latest`는 사용하지 않는다.
- 2026-11-08 실제 배포 후 공개 `http://13.125.158.52/` 및 `/api/health`를 외부에서 확인했고 둘 다 HTTP 200이었다. DNS는 별도로 확인하지 않았다.

## 신규 호스트 Bootstrap only (자동 재배포에는 사용하지 않음)

아래 수동 절차는 새 호스트를 별도 승인으로 준비할 때만 참고한다. 기존 dev-stud-server는 이미 서비스·DB·볼륨이 있는 것으로 확인했으므로, 자동 CD는 신규 설치나 데이터 준비를 수행하지 않는다.

두 저장소를 같은 상위 폴더에 SEOUL-WAVE, SEOUL-WAVE_admin 이름으로 배치한다. 각각 검토된 배포 커밋을 checkout한다. 실제 비밀값을 Git에 올리지 않는다.

- SEOUL-WAVE/.env.prod: .env.prod.example을 복사하고 빈 값과 REPLACE_*를 실제 값으로 교체한다.
- 신규 DB 비밀번호와 32자 이상의 SESSION_SECRET을 무작위로 생성한다. 볼륨 생성 이후 비밀번호를 임의 변경하지 않는다.
- HTTP 학원 시연은 사용자 승인에 따라 SESSION_COOKIE_SECURE=false를 사용한다. HTTP의 접속 비밀번호와 Basic 인증은 암호화되지 않으므로 학원 시연 범위에서 사용하고 HTTPS를 적용하면 true로 변경한다.
- ADMIN_API_KEY는 URL-safe 문자 32~128자 무작위 값이다. API와 관리자 Nginx에만 전달하며 브라우저 빌드에 포함하지 않는다.
- ADMIN_HTPASSWD_FILE은 저장소 밖의 절대 경로다. EC2에서 apache2-utils의 htpasswd -cB <파일경로> <관리자아이디>로 비밀번호를 대화형 입력한다. 비밀번호를 명령 인자나 로그에 남기지 않는다. 상위 전용 디렉터리는 700, 비밀번호 해시 파일은 644로 설정해 호스트에서는 다른 사용자가 상위 디렉터리에 접근하지 못하고, Docker에 마운트된 파일은 Nginx worker가 읽을 수 있게 한다.
- 장소 SQLite 원본을 서버의 전용 폴더에 업로드한다. PostgreSQL 볼륨을 복사하는 작업이 아니다. 원본은 읽기 전용으로 마운트해 적재한다.

## 명령 준비 (EC2 실행은 승인 후)

서비스 저장소 폴더에서 Bash 함수를 정의한다.

```bash
dc() { sudo docker compose --env-file .env.prod -f compose.prod.yaml -f ../SEOUL-WAVE_admin/compose.prod.admin.yaml "$@"; }
dc config --quiet
dc pull
```

config의 전체 출력을 공유하지 않는다. 비밀값이 펼쳐질 수 있다.

1. 컨테이너·볼륨·80/8080/8081 포트·메모리를 다시 확인한다. 기존 DB가 발견되면 이 신규 설치 절차를 중단한다.
2. 승인 후 dc up -d db로 DB만 시작한다. pg_isready와 free -h를 확인한다.
3. dc run --rm --no-deps backend python scripts/apply_migrations.py로 읽기 전용 계획을 확인한다.
4. 마이그레이션 승인 후 같은 명령 끝에 --apply를 추가한다. 도구는 체크섬과 적용 이력을 저장하며, 이력 없는 기존 테이블이 있거나 적용된 SQL이 바뀌면 거부한다. 각 SQL과 이력은 같은 트랜잭션으로 적용된다. 앱 시작 시에는 실행하지 않는다.
5. 장소 데이터는 아래처럼 dry-run 후 승인된 적재를 한다. /home/ubuntu/seoul-wave-data는 실제 업로드 폴더로 바꾼다.

```bash
dc run --rm --no-deps -v /home/ubuntu/seoul-wave-data:/source:ro backend python scripts/import_place_catalog.py --source /source/seoul_places.sqlite3 --dry-run
dc run --rm --no-deps -v /home/ubuntu/seoul-wave-data:/source:ro backend python scripts/import_place_catalog.py --source /source/seoul_places.sqlite3
```

6. dc up -d backend frontend → dc up -d itinerary-worker database-health-monitor → dc up -d admin-api admin-frontend 순서로 실행한다. 각 단계에서 dc ps, docker stats --no-stream, free -h를 확인한다. available 300MiB 미만 또는 OOM이 있으면 다음 서비스를 올리지 않는다. t3.small 실측 부하는 아직 확인되지 않았다.
7. 호스트 Nginx의 활성 설정을 백업하고 nginx-ec2.conf.example의 location을 기존 server block에 반영한다. sudo nginx -t 통과 후 승인받은 reload를 한다. 기존 Nginx를 중지하거나 80을 다른 컨테이너에 넘기지 않는다.
8. 외부 URL에서 사용자 로그인, 선착장·시간표·날짜 선택, 실제 LLM 추천, 재생성 횟수 제한, /admin/ 로그인과 실행 로그를 확인한다. /ready와 worker health는 실제 LLM 추천 성공을 보장하지 않는다.

## 데이터 보호와 롤백

현재 호스트에는 보존해야 하는 PostgreSQL 및 monitor-state 볼륨이 있다. 자동 배포는 DB schema나 데이터를 변경하지 않으며, 변경 전 앱 이미지 identity를 기록하고 앱 서비스만 복구한다. DB 백업·복원, migration, 장소 데이터 적재는 별도 승인 절차다. `down -v`, `volume rm`, `system prune`, 테이블 초기화는 사용하지 않는다.

## 검증 증빙

## 2026-11-08 실제 배포 검증 상태

PR #15가 squash merge되어 `dev`의 `2e0fad0931215787a4afd69faeee21c3618d31f2`에 반영되었다. CI run `42430497063`은 테스트·배포 gate·이미지 게시에 성공했고 검증 릴리스 Artifact를 만들었다. 첫 CD 시도는 GitHub OIDC subject 형식 불일치로 AWS 인증에서 멈춰 SSM 명령을 보내지 않았다. CloudTrail에서 확인한 불변 owner/repository ID를 포함한 정확한 `dev` subject로 IAM trust를 수정했다.

PR #15의 첫 실제 SSM 시도(`42430732165`, command `37587b0d-4449-4341-b283-4a286ad4d621`)는 호스트에서 `config_id_mismatch`를 감지해 자동 롤백했다. SSM 결과는 `rolled_back`이며 rollback 검증과 proxy health·protected API·login·timetable 검사가 모두 통과했다. 이 시도는 성공 배포가 아니었다.

PR #16은 `8ed75892336d2b30b408a4aeed82b4e96570536c`로 squash merge되었다. 검증 pipeline run `42433516487`은 백엔드·프론트엔드·통합 테스트, gate 및 이미지 게시에 성공했고, `verified-release` artifact를 배포 run `42433731035`가 사용했다. OIDC 인증과 고정 SSM 문서 전송도 성공했다. command `90f0ab47-8bcc-4f32-b774-46e6713b9a7e`의 최종 `runFixedDeployment` 상태는 `Success`, 응답 코드는 0, 원격 결과는 `outcome=applied`였다. 결과의 source SHA는 `8ed75892336d2b30b408a4aeed82b4e96570536c`, release run ID는 `42433516487`이며 backend/frontend config ID 확인, migration dry-run `none`, proxy health·protected API·login·timetable 점검이 모두 통과했다. 외부 공개 `/` 및 `/api/health`도 각각 HTTP 200(`health=ok`)이었다.

단, GitHub CD run `42433731035` 자체는 `ssm_poll_failed`로 종료되었다. CloudTrail에서 확인한 첫 `GetCommandInvocation` 호출은 SSM이 아직 `runFixedDeployment` plugin을 등록하기 전이라 `InvalidPluginName`을 반환했다. 당시 poller가 이 일시적 응답을 재시도 가능한 상태로 분류하지 않아 workflow가 먼저 실패했지만, 이미 접수된 SSM 명령은 계속 실행되어 위와 같이 실제 배포와 원격 검증을 완료했다. 따라서 운영 배포는 성공했으나 그 CD run의 GitHub 상태는 실패로 남았다. follow-up PR은 이 오류를 재시도하고, 선행 단계가 실제 진행 중이지 않은 상태에서 고정 invocation들이 120초간 계속 조회되지 않으면 안전한 실패 코드로 종료하도록 bounded retry와 회귀 테스트를 추가한다. 성공 화면 GIF는 workflow도 성공한 후 실제 브라우저 화면으로 검증해 README에 추가한다.
