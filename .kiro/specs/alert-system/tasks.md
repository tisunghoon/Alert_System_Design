# Implementation Plan: Alert System

## Overview

Python + FastAPI 기반의 분산 알림 플랫폼을 단계적으로 구현합니다.
프로젝트 초기 설정부터 시작하여 데이터베이스 스키마, Redis 인프라, Alert_Server API, Worker, Third_Party_Mock, 테스트 순서로 진행하며, 각 단계가 이전 단계 위에 빌드됩니다.

## Tasks

- [ ] 1. 프로젝트 초기 설정 및 인프라 구성
  - [ ] 1.1 프로젝트 디렉토리 구조 및 의존성 설정
    - `pyproject.toml` 또는 `requirements.txt`에 fastapi, uvicorn, sqlalchemy, asyncpg, redis, alembic, pytest, hypothesis, httpx, pytest-asyncio, fakeredis 의존성 추가
    - 다음 디렉토리 구조 생성: `app/`, `app/api/`, `app/core/`, `app/models/`, `app/schemas/`, `app/services/`, `app/workers/`, `app/mocks/`, `tests/unit/`, `tests/property/`, `tests/integration/`
    - `app/core/config.py`에 환경 변수 기반 설정 클래스 작성 (DATABASE_URL, REDIS_URL, RATE_LIMIT_DEFAULT 등)
    - _Requirements: 11.4_

  - [ ] 1.2 Docker Compose 설정
    - `docker-compose.yml`에 alert_server(FastAPI), worker_ios/android/sms/email(4개), postgresql, redis 서비스 정의
    - PostgreSQL 서비스: 포트 5432, 볼륨 마운트, 헬스체크 설정
    - Redis 서비스: 포트 6379, 볼륨 마운트, 헬스체크 설정
    - 각 서비스 간 depends_on 설정으로 시작 순서 보장
    - _Requirements: 3.1, 11.5_

- [ ] 2. 데이터베이스 스키마 및 마이그레이션
  - [ ] 2.1 Alembic 초기화 및 PostgreSQL 스키마 마이그레이션 작성
    - `alembic init alembic` 실행 후 `alembic/env.py` 설정
    - `apps`, `users`, `devices`, `user_preferences`, `notification_templates`, `notifications`, `notification_status_history` 테이블 DDL을 마이그레이션 파일로 작성
    - 설계 문서의 스키마 정의대로 CHECK 제약, UNIQUE 제약, 인덱스 포함
    - `notifications.expires_at` GENERATED ALWAYS AS 컬럼 포함 (queued_at + 30일)
    - _Requirements: 8.1, 8.7, 12.1_

  - [ ] 2.2 SQLAlchemy ORM 모델 정의
    - `app/models/` 디렉토리에 각 테이블에 대응하는 ORM 모델 클래스 작성
    - `Base`, `App`, `User`, `Device`, `UserPreference`, `NotificationTemplate`, `Notification`, `NotificationStatusHistory` 모델 정의
    - 관계(relationship) 및 외래 키 설정
    - _Requirements: 8.1, 8.2_

  - [ ]* 2.3 ORM 모델 단위 테스트 작성
    - `tests/unit/test_models.py`: 각 모델 인스턴스 생성, 제약 위반 시 예외 발생 확인
    - _Requirements: 8.1_

- [ ] 3. Redis 인프라 및 공통 유틸리티
  - [ ] 3.1 Redis 연결 및 Stream/Cache 유틸리티 구현
    - `app/core/redis.py`에 asyncio 기반 Redis 클라이언트 싱글톤 구현
    - Stream 관련 함수: `xadd_notification()`, `xreadgroup_messages()`, `xack_message()`, `get_stream_length()` 구현
    - Cache 관련 함수: `get_cache()`, `set_cache()`, `delete_cache()`, `incr_with_ttl()` 구현
    - Redis Streams Consumer Group 초기화 함수 구현 (`XGROUP CREATE` with MKSTREAM)
    - _Requirements: 3.1, 5.3, 7.2_

  - [ ] 3.2 데이터베이스 연결 및 세션 관리 구현
    - `app/core/database.py`에 asyncpg 기반 SQLAlchemy async 엔진 설정
    - `get_db()` FastAPI 의존성 함수 구현 (요청당 세션 생성 및 자동 커밋/롤백)
    - _Requirements: 12.1, 12.4_

- [ ] 4. 인증 및 핵심 미들웨어
  - [ ] 4.1 appKey/appSecret 인증 서비스 구현
    - `app/services/auth.py`에 `authenticate_app(app_key, app_secret, db)` 함수 구현
    - DB에서 `apps` 테이블 조회, `is_active` 확인, 일치하지 않으면 `None` 반환
    - FastAPI 의존성으로 사용 가능한 `get_current_app()` 함수 구현
    - _Requirements: 1.1, 1.2_

  - [ ]* 4.2 인증 서비스 단위 테스트 작성
    - `tests/unit/test_auth.py`: 올바른 appKey/appSecret으로 성공, 잘못된 값으로 실패, 비활성 앱 거부 테스트
    - _Requirements: 1.1, 1.2_

- [ ] 5. Rate_Limiter 구현
  - [ ] 5.1 Redis 기반 Rate_Limiter 구현
    - `app/services/rate_limiter.py`에 `check_rate_limit(user_id, channel, db)` 함수 구현
    - Redis `INCR rate:{user_id}:{channel}` + `EXPIRE 60` 패턴으로 카운터 관리
    - 사용자별 설정이 있으면 DB에서 조회하여 전역 기본값(60) 대신 적용
    - Redis 응답 불가 시 검사 건너뛰고 경고 로그 기록 후 허용
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 5.2 Rate_Limiter 속성 기반 테스트 작성
    - `tests/property/test_prop_rate_limit.py`
    - **Property 4: Rate Limit 강제 적용** - `st.integers(min_value=1, max_value=120)`으로 요청 수 생성, limit+1번째부터 429 반환 검증
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 5.3 Rate_Limiter 속성 기반 테스트 (사용자별 설정)
    - `tests/property/test_prop_rate_limit.py`에 추가
    - **Property 5: 사용자별 Rate Limit 설정 우선 적용** - `st.integers(min_value=1, max_value=100)`으로 사용자별/전역 값 조합 생성, 사용자별 값 우선 적용 검증
    - **Validates: Requirements 5.4**

- [ ] 6. 중복 방지(Deduplication) 서비스 구현
  - [ ] 6.1 Event_ID 중복 방지 서비스 구현
    - `app/services/deduplication.py`에 `check_duplicate(event_id, db)` 함수 구현
    - Redis `EXISTS dedup:{event_id}` 조회 → HIT이면 `True` 반환
    - Cache MISS 시 DB `notifications` 테이블에서 동일 event_id의 성공 기록 조회 (폴백)
    - `mark_processed(event_id)`: Redis에 `dedup:{event_id}` TTL 24시간으로 저장
    - _Requirements: 7.1, 7.2, 7.3_

  - [ ]* 6.2 중복 방지 속성 기반 테스트 작성
    - `tests/property/test_prop_deduplication.py`
    - **Property 3: Event_ID 중복 방지(멱등성)** - `st.text(min_size=1, max_size=256)`으로 Event_ID 생성, 동일 ID 두 번 전송 시 두 번째는 409 반환 검증
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4**

- [ ] 7. 알림 템플릿 서비스 구현
  - [ ] 7.1 템플릿 플레이스홀더 치환 서비스 구현
    - `app/services/template_service.py`에 `render_template(template, variables)` 함수 구현
    - `{{placeholder}}` 패턴 치환, 누락/초과 변수 감지 시 `TemplateVariableMismatchError` 발생
    - Cache-Aside 패턴: `template:{template_id}` 키로 Redis 조회 → MISS 시 DB 조회 후 캐시 저장 (TTL 10분)
    - _Requirements: 4.2, 4.3, 4.6_

  - [ ]* 7.2 템플릿 치환 속성 기반 테스트 작성
    - `tests/property/test_prop_template.py`
    - **Property 8: 템플릿 플레이스홀더 치환 라운드트립** - `st.dictionaries(st.text(), st.text())`로 변수 값 생성, 치환 결과에 원본 값이 모두 포함되고 미치환 플레이스홀더 없음 검증
    - **Validates: Requirements 4.2**

  - [ ]* 7.3 변수 불일치 거부 속성 기반 테스트 작성
    - `tests/property/test_prop_template.py`에 추가
    - **Property 9: 변수 불일치 시 알림 전송 거부** - `st.sets(st.text())`로 누락/초과 변수 세트 생성, 플레이스홀더 집합과 불일치 시 항상 거부 검증
    - **Validates: Requirements 4.3**

- [ ] 8. Third_Party_Mock 구현
  - [ ] 8.1 채널별 Third_Party_Mock 클래스 구현
    - `app/mocks/third_party_mock.py`에 `ThirdPartyMock` 클래스 구현
    - `success_rate: int = 100`, `delay_ms: int = 0`, `records: deque` (최대 10,000건 FIFO) 필드
    - `send(notification)` 메서드: 지연 적용 → 성공률 기반 무작위 결과 → 기록 저장 (10,000건 초과 시 oldest 삭제)
    - `reset()` 메서드: records 초기화, 설정 기본값 복원
    - `APNSMock`, `FCMMock`, `TwilioMock`, `SendGridMock` 채널별 클래스 구현 (ThirdPartyMock 상속)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [ ]* 8.2 Mock 성공률 속성 기반 테스트 작성
    - `tests/property/test_prop_mock.py`
    - **Property 10: Mock 성공률 확률적 일치성** - `st.integers(min_value=0, max_value=100)`으로 성공률 생성, 1,000회 전송 후 성공 비율이 설정값 ±5% 이내 수렴 검증
    - **Validates: Requirements 9.2**

  - [ ]* 8.3 Mock 인메모리 용량 제한 속성 기반 테스트 작성
    - `tests/property/test_prop_mock.py`에 추가
    - **Property 11: Mock 인메모리 저장 용량 제한** - `st.integers(min_value=10001, max_value=15000)`으로 건수 생성, records 길이가 항상 min(전체_건수, 10,000)임을 검증
    - **Validates: Requirements 9.6**

- [ ] 9. Checkpoint - 핵심 서비스 레이어 완료
  - 모든 서비스 레이어(인증, Rate Limit, 중복 방지, 템플릿, Mock) 단위 테스트 통과 확인
  - `pytest tests/unit/ tests/property/` 실행하여 오류 없음 확인
  - 문의사항이 있으면 사용자에게 질문하세요.

- [ ] 10. Notification_Log 서비스 및 WAL 패턴 구현
  - [ ] 10.1 Notification_Log 서비스 구현
    - `app/services/notification_log.py`에 다음 함수 구현
    - `create_queued_log(event_id, app_id, channel, recipient_id, title, body, template_id, db)`: QUEUED 상태 초기 기록 생성, 실패 시 예외 발생
    - `update_status(notification_id, status, worker_id, note, db)`: 상태 변경 + `notification_status_history` 기록
    - `set_final_duration(notification_id, db)`: queued_at 기준 경과 시간(ms) 계산 후 `total_duration_ms` 업데이트
    - `get_by_event_id(event_id, db)`: 이력 포함 전체 조회
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 10.2 WAL 패턴 속성 기반 테스트 작성
    - `tests/property/test_prop_wal.py`
    - **Property 12: WAL 패턴 - 큐 삽입 전 로그 선기록** - `st.builds(NotificationRequest)`로 임의 알림 요청 생성, 큐에 있는 모든 Notification이 DB에 QUEUED 기록을 먼저 가짐을 검증
    - **Validates: Requirements 12.1, 8.1**

  - [ ]* 10.3 처리 완료 후 큐 메시지 삭제 속성 기반 테스트 작성
    - `tests/property/test_prop_wal.py`에 추가
    - **Property 13: 처리 완료 후 큐 메시지 삭제** - `st.booleans()`으로 성공/실패 시나리오 생성, XACK된 메시지는 항상 DB에 최종 상태 기록을 가짐을 검증
    - **Validates: Requirements 12.3**

- [ ] 11. Alert_Server API 구현 - 알림 전송 핵심 플로우
  - [ ] 11.1 POST /notifications 엔드포인트 구현
    - `app/api/notifications.py`에 라우터 정의
    - 처리 순서: 인증 검증 → Rate Limit 검사 → Event_ID 중복 검사 → 필드 유효성 검증 → User_Preference 비활성 채널 스킵 → 템플릿 치환 → WAL: DB QUEUED 기록 → Event_ID Cache 저장 → Redis Stream XADD → HTTP 202 반환
    - Message_Queue 삽입 3초 타임아웃 → 실패 시 HTTP 503 반환
    - `app/schemas/notification.py`에 요청/응답 Pydantic 모델 정의
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.7, 7.1, 12.1, 12.2_

  - [ ]* 11.2 인증 유효성 검증 속성 기반 테스트 작성
    - `tests/property/test_prop_auth.py`
    - **Property 1: 유효한 인증 요청만 처리됨** - `st.text()`로 유효/무효 appKey·appSecret 생성, 유효한 경우만 처리되고 그 외 401/400 반환 검증
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 11.3 요청 필드 유효성 검증 속성 기반 테스트 작성
    - `tests/property/test_prop_auth.py`에 추가
    - **Property 2: 알림 요청 필드 유효성 검증** - `st.sampled_from()`, `st.text()`로 유효/무효 채널·길이 초과 필드 생성, 하나라도 유효하지 않으면 HTTP 400 + 실패 필드명 반환 검증
    - **Validates: Requirements 1.3, 1.4**

  - [ ] 11.4 GET /notifications/{event_id} 엔드포인트 구현
    - `notification_log.get_by_event_id()` 호출, 현재 상태 + 전체 상태 이력 + 채널/수신자/페이로드 반환
    - 존재하지 않는 event_id → HTTP 404 반환
    - _Requirements: 8.3, 8.4_

- [ ] 12. Alert_Server API 구현 - 사용자/단말/Preference 관리
  - [ ] 12.1 단말 관리 API 구현
    - `app/api/devices.py`에 라우터 정의
    - `POST /users/{user_id}/devices`: 채널 유효성 검증, 사용자당 최대 10개 제한 확인 후 저장, Cache 무효화
    - `GET /users/{user_id}/devices`: Cache-Aside 패턴 (`device:{user_id}`, TTL 5분)
    - `DELETE /users/{user_id}/devices/{device_id}`: 삭제 후 Cache 무효화
    - `app/schemas/device.py`에 Pydantic 모델 정의
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 12.2 User_Preference API 구현
    - `app/api/preferences.py`에 라우터 정의
    - `GET /users/{user_id}/preferences`: Cache-Aside 패턴 (`pref:{user_id}`, TTL 5분)
    - `PUT /users/{user_id}/preferences`: DB 업데이트 + Cache 무효화
    - `app/schemas/preference.py`에 Pydantic 모델 정의
    - _Requirements: 2.6, 2.7_

  - [ ]* 12.3 User_Preference 비활성 채널 스킵 속성 기반 테스트 작성
    - `tests/property/test_prop_preference.py`
    - **Property 14: User_Preference 비활성화 채널 스킵** - `st.booleans()`, `st.sampled_from(CHANNELS)`으로 채널 활성/비활성 조합 생성, 비활성 채널은 Third_Party_Mock에 요청 도달하지 않음 검증
    - **Validates: Requirements 2.7**

- [ ] 13. Alert_Server API 구현 - 템플릿 CRUD
  - [ ] 13.1 알림 템플릿 CRUD API 구현
    - `app/api/templates.py`에 라우터 정의
    - `POST /templates`: 제목 최대 200자, 본문 최대 10,000자, 플레이스홀더 최대 50개 검증 후 저장
    - `GET /templates/{template_id}`: Cache-Aside (`template:{template_id}`, TTL 10분)
    - `PUT /templates/{template_id}`: DB 업데이트 + Cache 무효화
    - `DELETE /templates/{template_id}`: `ref_count > 0`이면 HTTP 409 반환, 그 외 soft delete (`is_deleted = TRUE`)
    - `app/schemas/template.py`에 Pydantic 모델 정의
    - _Requirements: 4.1, 4.4, 4.5, 4.6_

- [ ] 14. Alert_Server API 구현 - 모니터링/DLQ/Mock 관리
  - [ ] 14.1 모니터링 및 통계 API 구현
    - `app/api/monitoring.py`에 라우터 정의
    - `GET /monitoring/queues`: Redis `XLEN` 으로 각 채널 Stream 길이 조회, 1,000ms 이내 반환, 임계값 초과 시 경고 로그
    - `GET /monitoring/stats`: 시간 범위 파라미터 검증 (누락 또는 30일 초과 시 400), DB에서 채널별 DELIVERED/FAILED/재시도 수 집계
    - `GET /health`: DB/Redis/Redis Streams 연결 상태 각각 확인, 3,000ms 이내 반환, 하나라도 비정상 시 HTTP 503
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 14.2 헬스체크 완전성 속성 기반 테스트 작성
    - `tests/property/test_prop_health.py`
    - **Property 15: 헬스체크 응답 완전성** - `st.booleans() × 3`으로 DB/Cache/MQ 정상/비정상 조합 생성, 응답이 세 컴포넌트 상태를 모두 포함하고 하나라도 비정상 시 503 반환 검증
    - **Validates: Requirements 10.5, 10.6**

  - [ ] 14.3 Dead Letter Queue 조회 API 구현
    - `GET /dead-letter`: `dead_letter_stream`에서 메시지 목록 조회, Notification_ID/최종실패시각/실패원인/누적재시도횟수 포함 반환, 비어 있으면 빈 목록 반환
    - _Requirements: 6.6, 6.7_

  - [ ] 14.4 Mock 관리 API 구현
    - `POST /mocks/reset`: 모든 채널 ThirdPartyMock `reset()` 호출
    - `PUT /mocks/{channel}/config`: 성공률(0-100), 지연(0-30000ms) 설정
    - `GET /mocks/{channel}/records`: 채널별 수신 기록 목록 반환
    - _Requirements: 9.7_

- [ ] 15. Checkpoint - Alert_Server API 완료
  - 모든 엔드포인트 동작 확인 (`pytest tests/unit/ tests/property/` 통과)
  - FastAPI 자동 생성 OpenAPI 문서(`/docs`)에서 엔드포인트 목록 확인
  - 문의사항이 있으면 사용자에게 질문하세요.

- [ ] 16. Worker 구현
  - [ ] 16.1 Worker 기반 클래스 및 처리 루프 구현
    - `app/workers/base_worker.py`에 `BaseWorker` 클래스 구현
    - `run()` 메서드: `XREADGROUP(count=10, block=1000ms)` 루프
    - `process_message(message)`: 이중 중복 확인(Notification_Log 조회) → `deliver()` 호출(5초 타임아웃) → 결과 로그 → `XACK`
    - 지수 백오프 재시도: `retry_count < 3`이면 `min(2^retry_count, 32)`초 지연 후 `retry_stream` XADD
    - `retry_count == 3`이면 `dead_letter_stream` XADD + Notification_Log FAILED 기록
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 6.1, 6.2, 6.3, 6.4, 6.5, 7.4, 11.2, 11.3, 12.3, 12.5_

  - [ ] 16.2 채널별 Worker 구현
    - `app/workers/ios_worker.py`, `android_worker.py`, `sms_worker.py`, `email_worker.py` 구현
    - 각 Worker가 `BaseWorker`를 상속하고 `deliver(notification)` 메서드에서 해당 채널 ThirdPartyMock 호출
    - Worker 진입점 스크립트 작성 (Docker Compose `command`로 실행)
    - _Requirements: 3.2, 3.6, 11.5_

  - [ ]* 16.3 재시도 횟수 단조 증가 속성 기반 테스트 작성
    - `tests/property/test_prop_retry.py`
    - **Property 6: 재시도 횟수 단조 증가** - `st.integers(min_value=0, max_value=3)`으로 실패 횟수 생성, 재시도 횟수가 정확히 1씩 증가하고 3회 도달 시 DLQ 이동 검증
    - **Validates: Requirements 6.1, 6.3, 3.4, 3.5**

  - [ ]* 16.4 지수 백오프 지연 속성 기반 테스트 작성
    - `tests/property/test_prop_retry.py`에 추가
    - **Property 7: 지수 백오프 지연 단조 증가** - `st.integers(min_value=1, max_value=3)`으로 재시도 회차 생성, 지연 시간이 `min(2^(n-1), 32)`초이고 n 증가 시 단조 증가(또는 32초 상한 유지) 검증
    - **Validates: Requirements 6.1**

- [ ] 17. 통합 및 E2E 테스트
  - [ ]* 17.1 정상 알림 전송 E2E 통합 테스트 작성
    - `tests/integration/test_e2e_notification.py`: POST /notifications → Stream XADD 확인 → Worker 처리 → DB DELIVERED 상태 확인 → Mock records 확인
    - `tests/integration/test_e2e_retry.py`: Mock 실패율 100% 설정 → 3회 재시도 → `dead_letter_stream` 이동 확인
    - _Requirements: 3.2, 3.3, 6.1, 6.3_

  - [ ]* 17.2 중복 방지 및 Rate Limit E2E 통합 테스트 작성
    - `tests/integration/test_e2e_dedup.py`: 동일 Event_ID 두 번 전송 → 두 번째 409 반환 확인
    - `tests/integration/test_e2e_rate_limit.py`: 61회 전송 → 61번째 429 반환 확인
    - _Requirements: 7.1, 7.2, 5.1, 5.2_

  - [ ]* 17.3 User_Preference 및 WAL 패턴 E2E 통합 테스트 작성
    - `tests/integration/test_e2e_notification.py`에 추가: 채널 비활성화 후 알림 전송 → Mock 수신 기록 없음 확인
    - WAL 패턴 검증: 큐 삽입 실패 시 DB에 QUEUED 기록만 남고 Stream에 없음 확인
    - _Requirements: 2.7, 12.1_

- [ ] 18. Final Checkpoint - 전체 시스템 완료
  - `pytest tests/` 실행하여 전체 테스트 통과 확인
  - `docker-compose up` 으로 전체 스택 정상 기동 확인
  - 문의사항이 있으면 사용자에게 질문하세요.

## Notes

- `*` 표시 서브태스크는 선택 사항으로 빠른 MVP 구현 시 건너뛸 수 있습니다
- 각 태스크는 특정 요구사항을 참조하여 추적 가능성을 보장합니다
- 속성 기반 테스트(hypothesis)는 설계 문서의 15개 Property를 모두 커버합니다
- 체크포인트(Task 9, 15, 18)에서 점진적 검증을 수행합니다
- Worker는 Docker Compose에서 채널별 독립 컨테이너로 실행되어 수평 확장 패턴을 시연합니다
- Redis Streams Consumer Group은 At-Least-Once 보장을 위해 `notification_consumers` 그룹명을 사용합니다

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "3.1", "3.2"] },
    { "id": 2, "tasks": ["2.2", "2.3"] },
    { "id": 3, "tasks": ["4.1", "5.1", "6.1", "7.1", "8.1", "10.1"] },
    { "id": 4, "tasks": ["4.2", "5.2", "5.3", "6.2", "7.2", "7.3", "8.2", "8.3", "10.2", "10.3"] },
    { "id": 5, "tasks": ["11.1", "12.1", "12.2", "13.1", "14.1", "14.3", "14.4"] },
    { "id": 6, "tasks": ["11.2", "11.3", "11.4", "12.3", "14.2"] },
    { "id": 7, "tasks": ["16.1"] },
    { "id": 8, "tasks": ["16.2", "16.3", "16.4"] },
    { "id": 9, "tasks": ["17.1", "17.2", "17.3"] }
  ]
}
```
