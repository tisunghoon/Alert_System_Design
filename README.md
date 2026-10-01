# Alert_System_Design

iOS, Android, SMS, 이메일 채널로 알림을 보내는 알림 시스템입니다. FastAPI 서버가 요청을 받아 Redis Stream 큐에 넣고, 채널별 워커가 꺼내서 전송합니다. 상태와 이력은 PostgreSQL에 저장합니다. 요구사항과 설계는 `.kiro/specs/alert-system/`에 있습니다.

## 실행 방법

```bash
cp .env.example .env
docker compose up --build
```

DB 마이그레이션은 `alembic upgrade head`로 적용합니다. `DATABASE_URL`은 `.env`에서 읽습니다.

워커(`app.workers.<channel>_worker`)는 구현 PR이 병합되기 전까지 기동되지 않습니다.

로컬 테스트:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/unit tests/property
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
