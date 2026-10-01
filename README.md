# Alert_System_Design

iOS, Android, SMS, 이메일 채널로 알림을 보내는 알림 시스템입니다. FastAPI 서버가 요청을 받아 Redis Stream 큐에 넣고, 채널별 워커가 꺼내서 전송합니다. 상태와 이력은 PostgreSQL에 저장합니다. 요구사항과 설계는 `.kiro/specs/alert-system/`에 있습니다.

## 실행 방법

```bash
cp .env.example .env
docker compose up --build
```

`docker compose up`은 postgres가 준비되면 `migrate` 서비스가 `alembic upgrade head`를 실행하고, 이어서 `seed` 서비스가 개발용 앱을 등록한 뒤 서버와 워커를 기동합니다. 로컬에서 직접 적용하려면 `alembic upgrade head`를 실행합니다(`DATABASE_URL`은 `.env`에서 읽습니다).

개발용 앱은 `.env`의 `SEED_APP_KEY`/`SEED_APP_SECRET`(기본값 `dev-app-key`/`dev-app-secret`)으로 만들며, 이미 있으면 건드리지 않습니다. 로컬 개발 전용 값이므로 운영에서는 사용하지 않습니다.

워커(`app.workers.<channel>_worker`)는 구현 PR이 병합되기 전까지 기동되지 않습니다.

로컬 테스트:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/unit tests/property
```

통합 테스트는 실제 PostgreSQL/Redis가 필요하며 `integration` 마커로 분리되어 있습니다. 테이블을 삭제하므로 DB 이름에 `test`가 들어간 전용 DB를 사용합니다.

```bash
docker compose up -d postgres redis
docker compose exec postgres createdb -U alert alert_test
DATABASE_URL=postgresql+asyncpg://alert:alert@localhost:5432/alert_test \
REDIS_URL=redis://localhost:6379/0 \
.venv/bin/pytest -m integration tests/integration
```

## 디렉터리 구조

```
app/
├── api/        # FastAPI 라우터
├── core/       # 설정 등 공통 모듈
├── models/     # SQLAlchemy 모델
├── schemas/    # Pydantic 스키마
├── services/   # 비즈니스 로직
├── workers/    # 채널별 워커
├── mocks/      # 외부 전송 서비스 Mock
└── main.py
tests/
├── unit/
├── property/
└── integration/   # docker compose 환경에서 실행
```
