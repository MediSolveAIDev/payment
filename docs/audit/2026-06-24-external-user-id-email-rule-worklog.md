# 2026-06-24 — external_user_id 이메일 전용 룰 강제

## 요청
"서버에서 이메일을 받아서 이메일을 사용하기 때문에, external_user_id에 이메일을 쓰는 것을 명확한 룰로 정해줘."

## 결정
external_user_id 는 **반드시 이메일**. 서버가 받는 즉시 **앞뒤 공백 제거 + 소문자**로 정규화해 저장·조회한다. 이메일 형식이 아니면 422로 거부. 대소문자/공백 차이로 인한 사용자 중복(중복 구독·카드)을 막는다.

## 구현
- **신규 헬퍼** `app/core/identifiers.py:normalize_external_user_id` — strip+lower+이메일정규식+255자 검증, 위반 시 `InputValidationError`(422).
- **요청 스키마**(`app/schemas/api.py`) — `SubscriptionCreateRequest`/`OneOffPaymentRequest`/`CardRegisterRequest`의 `external_user_id`에 `field_validator`로 정규화 적용. 설명·예시를 이메일로 변경.
- **경로 파라미터**(읽기/액션 8곳) — 정규화 후 조회: cards GET/DELETE, payments GET, subscriptions GET/pay/cancel/resume/add-days. (`app/api/v1/*.py`)
- **쓰기 서비스**(저장 키 일관성) — `create_subscription`·`register_or_replace_card`·`create_one_off_payment` 시작부에서 정규화. `subscriptions._validate_external_user_id`는 이메일 룰 위임으로 전환(직접 호출 방어선).
- **모델 주석**(subscription/card/payment) — "=이메일(소문자 정규화)" 명시.
- HMAC: 서명은 클라이언트가 보낸 **원본 경로** 기준이라 인증 후 정규화와 무관하게 동작(영향 없음).

## 매뉴얼·룰
- `docs/user_manual/13-service-api.md` 13.1에 "external_user_id는 반드시 이메일" 규칙 추가, 본문 예시(`user-123`→`user@example.com`) 및 필드표 갱신. 매뉴얼 재빌드(20문서).
- `CLAUDE.md` 규칙 섹션에 룰 1줄 추가.

## 테스트
- 기존 테스트가 비이메일 ID(`user-1`, `a1`, `ghost-user` 등)를 광범위 사용 → 이메일 룰로 깨짐.
- 변환 스크립트 2패스(리터럴/dict/f-string → @e.com, 같은 토큰의 positional·URL 경로·assertion 일관화) + not-found 경로·대문자 이메일·변수 기본값 소수 수동 수정.
- 결과: **638 passed**(redis는 로컬 6480 컨테이너 사용: `TEST_REDIS_URL=redis://localhost:6480/15`).

## 비고
- sample_service는 이미 `external_user_id=user.email`을 전송(`shop/views.py`) → 룰과 부합, 코드 변경 불필요(서버가 양방향 정규화하므로 대소문자 차이도 안전).
- 기존 운영 데이터에 비이메일/비정규화 external_user_id가 있다면 마이그레이션 필요(해당 시 별도 진행).
