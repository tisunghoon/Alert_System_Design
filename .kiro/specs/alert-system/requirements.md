# Requirements Document

## Introduction

알림 시스템(Alert System) 토이 프로젝트는 모바일 푸시 알림(iOS/Android), SMS, 이메일 세 가지 채널을 지원하는
대규모 분산 알림 플랫폼의 핵심 아키텍처 패턴을 학습하기 위한 프로젝트입니다.

실제 APNS, FCM, Twilio, SendGrid 대신 Mock/Stub 구현을 사용하여 알림 전송 흐름, 메시지 큐 기반 비동기 처리,
재시도 메커니즘, 중복 방지, 전송률 제한 등 핵심 설계 패턴을 구현하고 검증합니다.

---

## Glossary

- **Alert_Server**: 알림 요청을 수신하고 검증하여 메시지 큐에 삽입하는 핵심 서버 컴포넌트
- **Worker**: 메시지 큐에서 알림을 꺼내 서드파티 서비스(Mock)로 전달하는 작업 서버 컴포넌트
- **Message_Queue**: 알림 채널별 독립적인 메시지 큐 (iOS 큐, Android 큐, SMS 큐, Email 큐)
- **Notification_Provider**: Alert_Server에 알림 전송을 요청하는 서비스 또는 마이크로서비스
- **Third_Party_Mock**: 실제 APNS, FCM, Twilio, SendGrid를 대체하는 Mock/Stub 구현체
- **Notification**: 단일 알림 요청으로, 채널(iOS/Android/SMS/Email), 수신자, 내용을 포함하는 단위
- **Notification_Template**: 재사용 가능한 알림 본문 템플릿
- **Device**: 사용자의 단말 정보로 단말 토큰과 채널 유형을 포함
- **User_Preference**: 사용자의 채널별 알림 수신 동의 설정
- **Event_ID**: 중복 알림 방지를 위한 각 알림 요청의 고유 식별자
- **Rate_Limiter**: 단위 시간당 사용자별 알림 수신 횟수를 제한하는 컴포넌트
- **Notification_Log**: 알림 전송 내역과 상태를 저장하는 데이터 저장소
- **Cache**: 사용자 정보, 단말 정보, 알림 템플릿을 캐싱하는 인메모리 저장소
- **Dead_Letter_Queue**: 최대 재시도 횟수를 초과한 최종 실패 알림을 보관하는 큐

---

## Requirements

### Requirement 1: 알림 전송 API

**User Story:** 서비스 개발자로서, 알림 전송 API를 호출하여 사용자에게 알림을 보내고 싶습니다. 그래야 다양한 채널로 사용자에게 메시지를 전달할 수 있습니다.

#### Acceptance Criteria

1. THE Alert_Server SHALL appKey와 appSecret을 포함하고 각각 1자 이상 256자 이하인 요청에 대해서만 알림 전송을 처리한다
2. IF 요청에 appKey 또는 appSecret이 누락되거나 빈 문자열인 경우, THEN THE Alert_Server SHALL HTTP 401 응답과 함께 인증 실패를 나타내는 오류 메시지를 반환한다
3. WHEN Notification_Provider가 알림 전송 요청을 전송할 때, THE Alert_Server SHALL 수신자 ID(1자 이상 128자 이하), 채널 유형(ios/android/sms/email 중 하나), 알림 제목(1자 이상 256자 이하), 알림 본문(1자 이상 4096자 이하)의 유효성을 검증한다
4. IF 필수 필드(수신자 ID, 채널 유형, 알림 본문)가 누락되거나 채널 유형이 ios/android/sms/email 이외의 값인 경우, THEN THE Alert_Server SHALL HTTP 400 응답과 함께 유효성 검증에 실패한 필드명과 실패 사유를 포함한 오류 메시지를 반환한다
5. WHEN 유효한 알림 요청이 수신될 때, THE Alert_Server SHALL 요청에 시스템 전체에서 고유한 Event_ID를 부여하고 해당 채널의 Message_Queue에 삽입한다
6. WHEN 알림이 Message_Queue에 성공적으로 삽입될 때, THE Alert_Server SHALL HTTP 202 응답과 함께 Event_ID를 반환한다
7. IF Message_Queue 삽입이 3초 이내에 완료되지 않거나 실패한 경우, THEN THE Alert_Server SHALL HTTP 503 응답과 함께 서비스 일시 불가를 나타내는 오류 메시지를 반환하고 요청 데이터를 보존한다

---

### Requirement 2: 사용자 및 단말 정보 관리

**User Story:** 시스템 관리자로서, 사용자의 단말 정보와 알림 설정을 관리하고 싶습니다. 그래야 각 사용자에게 적절한 채널로 알림을 전달할 수 있습니다.

#### Acceptance Criteria

1. THE Alert_Server SHALL 한 명의 사용자에 대해 최대 10개의 Device를 등록할 수 있어야 하며, 등록된 Device 목록을 조회할 수 있어야 한다
2. WHEN 사용자가 단말을 등록할 때, THE Alert_Server SHALL 채널 유형(ios/android/sms/email 중 하나)과 해당 채널에 대응하는 단말 식별 토큰(디바이스 토큰, 전화번호, 이메일 주소)을 저장한다
3. IF 단말 등록 요청의 채널 유형이 ios/android/sms/email 중 하나가 아닌 경우, THEN THE Alert_Server SHALL 해당 등록 요청을 거부하고 유효하지 않은 채널 유형임을 나타내는 오류 응답을 반환한다
4. WHEN 단말 정보 조회 요청이 수신될 때, THE Alert_Server SHALL Cache에 해당 사용자의 단말 정보가 존재하면 Cache에서 응답하고, Cache에 존재하지 않으면 데이터베이스에서 조회한 후 Cache에 저장하고 응답한다
5. WHEN 단말 정보가 수정 또는 삭제될 때, THE Alert_Server SHALL 해당 사용자의 Cache 항목을 즉시 갱신하거나 삭제하여 이후 조회 시 최신 단말 정보가 반영되도록 한다
6. THE Alert_Server SHALL 사용자별 User_Preference(채널별 알림 수신 동의 여부)를 저장하고 조회할 수 있어야 한다
7. WHILE 사용자의 특정 채널에 대한 User_Preference가 비활성화 상태인 동안, THE Alert_Server SHALL 해당 채널로의 알림 전송을 수행하지 않고 해당 채널을 건너뛴다

---

### Requirement 3: 채널별 메시지 큐 및 Worker 처리

**User Story:** 시스템 운영자로서, 알림이 채널별로 독립적인 큐를 통해 비동기 처리되기를 원합니다. 그래야 특정 채널의 장애가 다른 채널에 영향을 미치지 않도록 느슨하게 결합할 수 있습니다.

#### Acceptance Criteria

1. THE Message_Queue SHALL iOS, Android, SMS, Email 채널에 대해 각각 독립적인 큐를 유지하며, 한 채널 큐의 오류 또는 지연이 다른 채널 큐의 메시지 처리에 영향을 미치지 않아야 한다
2. WHEN Worker가 Message_Queue에서 Notification을 꺼낼 때, THE Worker SHALL 해당 채널의 Third_Party_Mock에 알림 전달을 시도한다
3. WHEN Third_Party_Mock이 알림 전달에 성공할 때, THE Worker SHALL 채널 식별자, 수신자 식별자, 전송 성공 상태, 타임스탬프를 포함하여 Notification_Log에 기록한다
4. IF Third_Party_Mock이 알림 전달에 실패하고 누적 재시도 횟수가 3회 미만인 경우, THEN THE Worker SHALL 해당 Notification을 재시도 큐에 삽입한다
5. IF Third_Party_Mock이 알림 전달에 실패하고 누적 재시도 횟수가 3회에 도달한 경우, THEN THE Worker SHALL 해당 Notification을 Dead_Letter_Queue로 이동하고 Notification_Log에 최종 실패 상태를 기록한다
6. THE Worker SHALL 각 채널에 대해 다른 채널의 인스턴스 수와 독립적으로 인스턴스 수를 조정할 수 있어야 한다

---

### Requirement 4: 알림 템플릿 관리

**User Story:** 콘텐츠 담당자로서, 재사용 가능한 알림 템플릿을 관리하고 싶습니다. 그래야 일관된 형식의 알림을 효율적으로 발송할 수 있습니다.

#### Acceptance Criteria

1. THE Alert_Server SHALL 알림 템플릿(제목 최대 200자, 본문 최대 10,000자, 플레이스홀더 최대 50개)을 생성, 조회, 수정, 삭제할 수 있어야 한다
2. WHEN 알림 전송 요청에 템플릿 ID와 변수 값이 포함될 때, THE Alert_Server SHALL 해당 템플릿의 플레이스홀더를 제공된 변수 값으로 치환하여 최종 알림 내용을 생성한다
3. IF 알림 전송 요청에 포함된 변수 값이 템플릿에 정의된 플레이스홀더와 일치하지 않는 경우, THEN THE Alert_Server SHALL 알림 전송을 중단하고 누락되거나 초과된 변수 항목을 명시하는 오류 메시지를 반환한다
4. IF 존재하지 않는 템플릿 ID가 요청에 포함된 경우, THEN THE Alert_Server SHALL 요청을 거부하고 해당 템플릿 ID를 찾을 수 없음을 나타내는 HTTP 404 오류 메시지를 반환한다
5. IF 활성 알림 규칙에서 참조 중인 템플릿에 대해 삭제 요청이 수신된 경우, THEN THE Alert_Server SHALL 삭제를 거부하고 해당 템플릿을 참조하는 규칙 수를 명시하는 오류 메시지를 반환한다
6. THE Alert_Server SHALL Notification_Template을 Cache에 저장하여 반복 렌더링 시 데이터베이스 조회 없이 처리한다

---

### Requirement 5: 전송률 제한 (Rate Limiting)

**User Story:** 시스템 운영자로서, 특정 사용자에게 과도한 알림이 전송되는 것을 방지하고 싶습니다. 그래야 사용자 경험을 보호하고 서드파티 서비스 비용을 제어할 수 있습니다.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL 사용자별로 채널당 분당 최대 알림 전송 횟수를 설정하고 강제할 수 있어야 하며, 전역 기본값은 분당 60회로 한다
2. WHEN 특정 사용자에 대한 알림 요청이 설정된 Rate Limit(분당 허용 횟수)에 도달하여 limit+1번째 요청부터 초과 상태가 될 때, THE Alert_Server SHALL 해당 알림을 Message_Queue에 삽입하지 않고 HTTP 429 응답을 반환한다
3. THE Rate_Limiter SHALL Rate Limit 카운터를 Cache에 60초 TTL과 함께 저장하여 60초 윈도우 만료 시 카운터를 0으로 자동 초기화한다
4. WHERE Rate Limit 설정이 사용자별로 구성된 경우, THE Rate_Limiter SHALL 전역 기본값 대신 사용자별 설정을 우선 적용한다
5. IF Cache가 응답 불가 상태인 경우, THEN THE Rate_Limiter SHALL Rate Limit 검사를 건너뛰고 요청을 허용하되, 해당 상황을 경고 로그에 기록한다

---

### Requirement 6: 재시도 메커니즘

**User Story:** 시스템 운영자로서, 알림 전송 실패 시 자동으로 재시도되기를 원합니다. 그래야 일시적인 서드파티 서비스 장애로 인한 알림 유실을 방지할 수 있습니다.

#### Acceptance Criteria

1. WHEN Worker가 Third_Party_Mock 전달에 실패할 때, THE Worker SHALL 해당 Notification의 재시도 횟수가 3회 미만인 경우 지수 백오프 지연(초기 지연 1초, n회차 지연 = 2^(n-1)초, 최대 지연 32초) 후 재시도 큐에 삽입한다
2. WHEN Worker가 Third_Party_Mock 전달에 실패할 때, THE Worker SHALL Notification_Log에 실패 이벤트, 현재 재시도 횟수, 다음 재시도 예정 시각을 기록한다
3. IF 재시도 횟수가 3회에 도달한 경우, THEN THE Worker SHALL 해당 Notification을 Dead_Letter_Queue로 이동한다
4. IF 재시도 횟수가 3회에 도달한 경우, THEN THE Worker SHALL Notification_Log에 최종 실패 상태와 실패 원인을 기록한다
5. WHEN Notification이 재시도 큐에서 처리될 때, THE Worker SHALL 재시도 횟수를 1 증가시키고 Notification_Log에 재시도 시작 이벤트와 현재 재시도 횟수를 기록한다
6. WHEN Alert_Server가 데드레터 큐 조회 요청을 수신할 때, THE Alert_Server SHALL Dead_Letter_Queue에 존재하는 Notification 목록을 Notification_ID, 최종 실패 시각, 실패 원인, 누적 재시도 횟수를 포함하여 반환한다
7. IF 데드레터 큐가 비어 있는 경우, THEN THE Alert_Server SHALL 빈 목록을 포함한 성공 응답을 반환한다

---

### Requirement 7: 중복 알림 방지

**User Story:** 사용자로서, 동일한 알림을 중복으로 수신하지 않기를 원합니다. 그래야 알림 시스템에 대한 신뢰를 유지할 수 있습니다.

#### Acceptance Criteria

1. WHEN Alert_Server가 이미 Cache에 존재하는 Event_ID를 가진 알림 요청을 수신할 때, THE Alert_Server SHALL 해당 요청에 대해 HTTP 409 응답을 반환하고 추가 처리를 수행하지 않는다
2. WHEN Alert_Server가 신규 Event_ID를 가진 알림 요청을 성공적으로 수신할 때, THE Alert_Server SHALL 해당 Event_ID를 24시간 TTL과 함께 Cache에 저장한다
3. IF Cache가 응답 불가 상태일 때, THEN THE Alert_Server SHALL Notification_Log를 조회하여 동일한 Event_ID의 성공 처리 기록 존재 여부를 확인하고, 기록이 존재하면 HTTP 409 응답을 반환하며 기록이 없으면 요청을 정상 처리한다
4. WHEN Worker가 Message_Queue에서 Event_ID를 가진 Notification을 처리하려 할 때, THE Worker SHALL Notification_Log에서 해당 Event_ID의 성공 전달 기록을 확인하여, 기록이 존재하면 처리를 건너뛰고 기록이 없으면 전달을 진행한다
5. IF Worker가 Notification_Log 조회에 실패할 때, THEN THE Worker SHALL 해당 Notification 처리를 중단하고 Message_Queue에 재시도 대기 상태로 반환하며, 최대 3회 재시도 이후에도 조회가 실패하면 해당 Notification을 Dead_Letter_Queue로 이동시킨다

---

### Requirement 8: 알림 로그 및 상태 추적

**User Story:** 서비스 운영자로서, 모든 알림의 전송 내역과 상태를 추적하고 싶습니다. 그래야 데이터 손실 없이 알림 이력을 감사(audit)하고 장애를 분석할 수 있습니다.

#### Acceptance Criteria

1. WHEN Alert_Server가 알림 요청을 수신할 때, THE Alert_Server SHALL Event_ID, 수신 타임스탬프, 채널(Channel), 수신자(Recipient), 메시지 페이로드, 초기 상태(QUEUED)를 포함하여 Notification_Log에 기록한다
2. WHEN Notification의 상태가 변경될 때(QUEUED → PROCESSING → DELIVERED/FAILED), THE Worker SHALL 변경된 상태(Status), 상태 변경 타임스탬프, 해당 상태로 전환한 Worker_ID를 Notification_Log에 기록한다
3. WHEN 운영자가 Event_ID로 알림 이력을 조회할 때, THE Alert_Server SHALL 해당 Notification의 현재 상태, 모든 상태 전환 이력(상태값 및 각 상태의 타임스탬프 포함), 채널, 수신자, 메시지 페이로드를 응답으로 반환한다
4. IF 조회된 Event_ID에 해당하는 Notification_Log 항목이 존재하지 않으면, THEN THE Alert_Server SHALL 해당 Event_ID를 찾을 수 없음을 나타내는 HTTP 404 오류 응답을 반환한다
5. WHEN 알림 전달이 완료(DELIVERED 또는 최종 FAILED 상태 도달)될 때, THE Worker SHALL QUEUED 상태 기록 시점부터 최종 상태 도달 시점까지의 경과 시간(밀리초 단위)을 총 처리 시간으로 Notification_Log에 기록한다
6. IF Notification_Log 기록 작업이 실패하면, THEN THE Alert_Server SHALL 알림 요청 처리를 중단하고 요청자에게 로그 기록 실패를 나타내는 오류 응답을 반환한다
7. THE Notification_Log SHALL DELIVERED 또는 최종 FAILED 상태 도달 시점을 기준으로 최소 30일간 해당 기록을 보존한다

---

### Requirement 9: Mock 서드파티 서비스

**User Story:** 개발자로서, 실제 외부 서비스 없이도 전체 알림 전송 흐름을 테스트하고 싶습니다. 그래야 비용 없이 핵심 아키텍처 패턴을 학습하고 검증할 수 있습니다.

#### Acceptance Criteria

1. THE Third_Party_Mock SHALL iOS(APNS Mock), Android(FCM Mock), SMS(Twilio Mock), Email(SendGrid Mock) 채널 각각에 대한 독립적인 Mock 구현체를 제공한다
2. THE Third_Party_Mock SHALL 성공률을 0 이상 100 이하의 정수(%)로 설정할 수 있어야 하며, 설정된 성공률에 따라 해당 비율만큼 성공 응답을, 나머지 비율만큼 실패 응답을 반환한다
3. THE Third_Party_Mock SHALL 지연 시간을 0ms 이상 30,000ms 이하의 정수(ms)로 설정할 수 있어야 하며, 설정된 지연 시간만큼 응답을 지연시켜 네트워크 지연 시나리오를 시뮬레이션할 수 있어야 한다
4. WHEN Third_Party_Mock이 알림을 수신할 때, THE Third_Party_Mock SHALL 수신 시각(ISO 8601 형식), 채널 유형, 수신자 식별자, 메시지 본문을 포함한 알림 내역을 인메모리에 저장하여 테스트 검증에 활용할 수 있어야 한다
5. THE Third_Party_Mock SHALL 채널별로 최대 10,000건까지 저장된 알림 목록을 조회하는 인터페이스를 제공하여 통합 테스트에서 전달 여부를 확인할 수 있어야 한다
6. IF Third_Party_Mock의 인메모리 알림 저장 건수가 채널별 10,000건을 초과할 때, THEN THE Third_Party_Mock SHALL 가장 오래된 항목부터 순서대로 삭제하여 최신 10,000건만 유지한다
7. WHEN 테스트 케이스가 초기화를 요청할 때, THE Third_Party_Mock SHALL 모든 채널의 인메모리 저장 내역을 초기화하고 성공률 및 지연 시간 설정을 기본값(성공률 100%, 지연 시간 0ms)으로 되돌린다

---

### Requirement 10: 시스템 모니터링

**User Story:** 시스템 운영자로서, 각 채널 큐의 적체 상태와 시스템 전반의 처리 현황을 모니터링하고 싶습니다. 그래야 병목 구간을 파악하고 Worker 규모를 조정하는 의사결정을 내릴 수 있습니다.

#### Acceptance Criteria

1. THE Alert_Server SHALL 각 채널(iOS, Android, SMS, Email) 메시지 큐의 현재 적재된 메시지 수를 조회하는 모니터링 엔드포인트를 제공하며, 해당 엔드포인트는 각 채널의 큐 이름과 현재 메시지 수를 포함한 응답을 1,000ms 이내에 반환한다
2. THE Alert_Server SHALL 최대 30일 범위의 시간 구간을 입력받아 채널별(iOS, Android, SMS, Email) 알림 전송 성공 수, 실패 수, 재시도 수를 집계하여 반환하는 통계 API를 제공한다
3. IF 시간 범위 파라미터가 누락되거나 30일을 초과하는 경우, THEN THE Alert_Server SHALL 유효하지 않은 시간 범위임을 나타내는 오류 응답을 반환한다
4. WHEN 특정 채널의 큐 크기가 설정된 임계값을 초과할 때, THE Alert_Server SHALL 채널명, 현재 큐 크기, 설정된 임계값을 포함한 경고 로그를 생성한다
5. THE Alert_Server SHALL 시스템 헬스 체크 엔드포인트를 제공하여 데이터베이스, 캐시, 메시지 큐 각각의 연결 상태(정상/비정상)를 포함한 응답을 3,000ms 이내에 반환한다
6. IF 헬스 체크 대상(데이터베이스, 캐시, 메시지 큐) 중 하나 이상이 비정상 상태인 경우, THEN THE Alert_Server SHALL 비정상 상태인 구성 요소를 명시한 오류 응답을 반환한다

---

### Requirement 11: 성능 및 확장성

**User Story:** 시스템 설계자로서, 알림 시스템이 수평 확장 가능한 구조로 설계되기를 원합니다. 그래야 토이 프로젝트에서 대규모 분산 시스템의 핵심 패턴을 학습할 수 있습니다.

#### Acceptance Criteria

1. WHEN Alert_Server가 알림 전송 요청을 수신할 때, THE Alert_Server SHALL 500ms 이내에 HTTP 응답을 반환한다
2. WHEN Worker가 Message_Queue에서 Notification을 꺼낼 때, THE Worker SHALL Third_Party_Mock 전달까지의 처리를 5초 이내에 완료한다
3. IF Worker가 5초 이내에 처리를 완료하지 못한 경우, THEN THE Worker SHALL 해당 처리를 타임아웃으로 기록하고 Notification을 재시도 큐에 삽입한다
4. THE Alert_Server SHALL 로컬 인스턴스에 세션, 인증 토큰, 처리 중간 상태 등 요청 간 공유 데이터를 보유하지 않는 Stateless 구조로 설계되어야 한다
5. THE Worker SHALL 이메일, SMS, 푸시(iOS/Android) 각 채널별로 독립적인 인스턴스를 실행할 수 있어야 하며, 특정 채널 Worker의 중단이 다른 채널 Worker에 영향을 미치지 않아야 한다

---

### Requirement 12: 데이터 무결성 및 신뢰성

**User Story:** 시스템 운영자로서, 알림이 유실 없이 처리되기를 원합니다. 그래야 연성 실시간성(soft real-time) 요구사항을 충족하는 신뢰할 수 있는 시스템을 구현할 수 있습니다.

#### Acceptance Criteria

1. WHEN Alert_Server가 Notification 요청을 수신할 때, THE Alert_Server SHALL Message_Queue에 Notification을 삽입하기 전에 Notification_Log에 상태를 QUEUED로 초기 기록을 생성한다
2. IF Notification_Log 초기 기록 생성이 실패할 경우, THEN THE Alert_Server SHALL Notification 처리를 중단하고 호출자에게 오류를 나타내는 에러 응답을 반환한다
3. WHEN Worker가 Message_Queue에서 Notification을 꺼낼 때, THE Worker SHALL Notification 처리 결과(성공 또는 최종 실패)를 Notification_Log에 기록한 후에만 Message_Queue에서 해당 메시지를 삭제한다
4. IF 데이터베이스 또는 Cache 연결이 일시적으로 불가할 경우, THEN THE Alert_Server SHALL 진행 중인 Notification의 상태를 변경하지 않고 호출자에게 HTTP 503 응답을 반환한다
5. IF Worker가 Notification 처리 중 데이터베이스 또는 외부 채널 연결 오류가 발생할 경우, THEN THE Worker SHALL 최대 3회까지 재시도하며, 3회 모두 실패 시 Notification_Log의 상태를 FAILED로 기록하고 해당 메시지를 Dead_Letter_Queue로 이동한다
