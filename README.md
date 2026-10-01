# Alert_System_Design

iOS, Android, SMS, 이메일 네 채널로 알림을 보내는 알림 시스템입니다. FastAPI 서버가 요청을 검증해 PostgreSQL에 기록한 뒤 Redis Stream에 넣고, 채널별 Worker가 꺼내 전송하는 구조입니다. 외부 전송 서비스는 Mock으로 대체했습니다.

요구사항과 설계는 [`.kiro/specs/alert-system/`](.kiro/specs/alert-system/)의 `requirements.md`, `design.md`, `tasks.md`에 있습니다.

## 요청 처리 흐름

`POST /notifications`는 아래 순서로 처리합니다.

1. appKey/appSecret 인증 (실패 401)
2. Rate Limit 검사: 사용자·채널별 분당 한도, 기본 60 (초과 429, `Retry-After` 헤더)
3. event_id 중복 검사 (409). event_id를 보내지 않으면 서버가 생성합니다.
4. 필드 유효성 검증 (400, 실패한 필드명과 사유 포함)
5. 수신 설정이 꺼진 채널이면 큐에 넣지 않고 `200 {"status": "skipped"}`로 응답
6. template_id가 있으면 템플릿 치환
7. Notification_Log에 QUEUED 상태로 먼저 기록하고 commit
8. 채널별 Redis Stream에 XADD (3초 안에 끝나지 않으면 503, QUEUED 로그는 남김)
9. 202 `{event_id, status: "queued", queued_at}`

큐에 넣기 전에 로그를 먼저 남기고, Worker는 결과를 기록한 뒤에만 XACK하는 방식(WAL 패턴)으로 알림이 유실되지 않게 했습니다.

## Worker

채널마다 하나씩 `python -m app.workers.<ios|android|sms|email>_worker`로 실행하며, compose에서는 `worker_*` 서비스가 이 명령을 씁니다. 각 Worker는 자기 채널 Stream을 컨슈머 그룹으로 읽어 Mock에 전송하고, 상태 변화를 `notification_status_history`에 남깁니다(QUEUED → PROCESSING → DELIVERED/FAILED).

- **재시도**: 전송에 실패하면 `retry_stream`에 넣어 다시 시도합니다. 최초 전송 뒤 최대 3회 재시도하므로 한 알림은 총 4번까지 전송됩니다. 재시도 간격은 1, 2, 4초이고(`2^(n-1)`초, 상한 32초), 재시도 대기 중인 알림은 QUEUED 상태로 이력에 사유와 다음 시각이 남습니다.
- **Dead Letter**: 재시도를 모두 쓰면 상태를 FAILED로 바꾸고 `dead_letter_stream`에 넣습니다. `GET /dead-letter`로 조회합니다.
- **컨슈머 이름**: 컨슈머 그룹은 `notification_consumers`, 컨슈머 이름은 기본적으로 채널 이름(`sms` 등)이며 `WORKER_ID` 환경 변수로 바꿀 수 있습니다. 같은 채널 Worker를 여러 개 띄울 때는 인스턴스마다 서로 다른 `WORKER_ID`를 줘야 합니다. 이름을 고정해 두었기 때문에 재시작하면 이전에 처리 중이던 메시지를 이어받습니다.
- **종료**: SIGTERM/SIGINT를 받으면 지금 처리 중인 메시지를 마친 뒤 종료합니다. 읽어 둔 나머지 메시지는 ack되지 않은 채 남아 다음 기동 때 이어서 처리됩니다.
- **Mock 설정**: `PUT /mocks/{channel}/config`로 정한 성공률·지연은 Redis에 저장되어 별도 프로세스인 Worker에도 적용됩니다. 예를 들어 `{"success_rate": 0}`을 주면 해당 채널 알림은 재시도 후 Dead Letter로 갑니다.

Redis는 Rate Limit 카운터, event_id 중복 캐시, 템플릿·단말·수신 설정 캐시, Stream에 사용합니다. `POST /notifications`는 Redis에 접근할 수 없으면 Rate Limit 검사를 건너뛰고, 중복 검사와 템플릿 조회는 DB로 대체합니다.

## 실행 방법

```bash
cp .env.example .env
docker compose up --build
```

`postgres`가 준비되면 `migrate` 서비스가 `alembic upgrade head`를 실행하고, 이어서 `seed` 서비스가 개발용 앱을 등록합니다. 둘이 끝난 뒤 서버와 채널별 Worker 4개가 기동합니다. `/health`가 `healthy`를 반환하면 준비된 것입니다.

- 개발용 앱은 `.env`의 `SEED_APP_KEY`/`SEED_APP_SECRET`(기본값 `dev-app-key`/`dev-app-secret`)으로 만들며, 이미 있으면 건드리지 않습니다. 로컬 개발 전용 값이므로 운영에서는 사용하지 않습니다.
- 호스트 포트는 `8000`(서버), `5432`(PostgreSQL), `6379`(Redis)로 고정되어 있어, 이미 쓰고 있으면 먼저 정리해야 합니다.
- 0001 마이그레이션이 수정된 적이 있어서 이전 버전으로 만든 `pgdata` 볼륨이 남아 있으면 `docker compose down -v`로 볼륨을 지우고 다시 올려야 합니다.
- 설정은 환경 변수 또는 `.env`로 읽습니다: `DATABASE_URL`, `REDIS_URL`, `RATE_LIMIT_DEFAULT`, `QUEUE_ALERT_THRESHOLD`, `SEED_APP_KEY`, `SEED_APP_SECRET` (`.env.example` 참고)

호스트에서 직접 실행하려면 인프라만 compose로 올립니다. Worker는 별도 터미널에서 `.venv/bin/python -m app.workers.sms_worker`처럼 실행합니다.

```bash
docker compose up -d postgres redis
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_app.py
.venv/bin/uvicorn app.main:app --port 8000
```

## API

인증이 있는 API는 appKey/appSecret을 요청 본문 JSON의 `app_key`, `app_secret`으로 보내거나, 본문이 없는 요청은 `X-App-Key`, `X-App-Secret` 헤더로 보냅니다. 둘 다 있으면 본문이 우선입니다. 오류 응답은 `{"error": {"code", "message", "details"}, "request_id"}` 형식입니다.

| 메서드 | 경로 | 인증 | 설명 | 응답 코드 |
|---|---|---|---|---|
| POST | `/notifications` | 필요 | 알림 전송 요청 | 202, 200(skipped), 400, 401, 404(템플릿), 409, 429, 503 |
| GET | `/notifications/{event_id}` | 필요 | 알림 상태와 전체 이력 조회. 다른 앱의 알림은 404 | 200, 401, 404 |
| POST | `/users/{user_id}/devices` | 필요 | 단말 등록 (사용자당 최대 10개) | 201, 400, 401, 409 |
| GET | `/users/{user_id}/devices` | 필요 | 단말 목록 | 200, 401 |
| DELETE | `/users/{user_id}/devices/{device_id}` | 필요 | 단말 삭제 | 204, 401, 404 |
| GET | `/users/{user_id}/preferences` | 필요 | 채널별 수신 설정 조회 | 200, 401 |
| PUT | `/users/{user_id}/preferences` | 필요 | 수신 설정 수정 | 200, 400, 401 |
| POST | `/templates` | 필요 | 템플릿 생성 | 201, 400, 401, 409 |
| GET | `/templates/{template_id}` | 필요 | 템플릿 조회 | 200, 401, 404 |
| PUT | `/templates/{template_id}` | 필요 | 템플릿 수정 | 200, 400, 401, 404, 409 |
| DELETE | `/templates/{template_id}` | 필요 | 템플릿 삭제 (참조 중이면 거부) | 204, 401, 404, 409 |
| GET | `/dead-letter` | 없음 | Dead Letter 목록 (`limit` 1~1000) | 200 |
| GET | `/monitoring/queues` | 없음 | 채널별 큐 크기 | 200 |
| GET | `/monitoring/stats` | 없음 | 채널별 전송 통계 (`start`, `end` 필수, 최대 30일) | 200, 400 |
| GET | `/health` | 없음 | DB, Redis, 큐 상태 | 200, 503 |
| POST | `/mocks/reset` | 없음 | Mock 설정과 수신 기록 초기화 | 200 |
| PUT | `/mocks/{channel}/config` | 없음 | Mock 성공률(0~100)과 지연(ms) 설정 | 200, 400, 404 |
| GET | `/mocks/{channel}/records` | 없음 | Mock이 받은 알림 기록 | 200, 404 |

`/docs`에서 FastAPI가 생성한 OpenAPI 문서를 볼 수 있습니다.

## 테스트

```bash
.venv/bin/pytest tests/unit tests/property
```

- `tests/unit`: 단위 테스트. DB는 mock, Redis는 fakeredis를 사용합니다.
- `tests/property`: hypothesis 기반 속성 테스트. 설계서의 Property를 검증합니다.
- `tests/integration`: 실제 PostgreSQL/Redis가 필요한 통합 테스트이며 `integration` 마커로 분리되어 있습니다.

통합 테스트는 실제 PostgreSQL/Redis가 필요하며 `integration` 마커로 분리되어 있습니다. 테이블을 삭제하므로 DB 이름에 `test`가 들어간 전용 DB를 사용합니다.

```bash
docker compose up -d postgres redis
docker compose exec postgres createdb -U alert alert_test
DATABASE_URL=postgresql+asyncpg://alert:alert@localhost:5432/alert_test \
REDIS_URL=redis://localhost:6379/0 \
.venv/bin/pytest -m integration tests/integration
```

CI(GitHub Actions)는 PR마다 단위·속성 테스트를 실행합니다.

## 디렉터리 구조

```
app/
├── api/        # FastAPI 라우터 (notifications, devices, preferences, templates, monitoring, dead_letter, mocks)
├── core/       # 설정, DB 세션, Redis 유틸, 오류 핸들러
├── models/     # SQLAlchemy 모델
├── schemas/    # Pydantic 스키마
├── services/   # 인증, Rate Limit, 중복 방지, 템플릿, Notification_Log 등
├── workers/    # BaseWorker와 채널별 Worker
├── mocks/      # 채널별 Third_Party_Mock와 설정 저장소
└── main.py
alembic/        # 마이그레이션
scripts/        # 개발용 앱 시드(seed_app.py)
tests/
├── unit/
├── property/
└── integration/
```
