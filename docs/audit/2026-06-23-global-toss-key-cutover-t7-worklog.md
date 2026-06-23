# 워크로그: 전역 TOSS_SECRET_KEY 제거(T7 컷오버) + 에러 응답 매핑 확인

- **날짜**: 2026-06-23
- **작업자**: Claude (Task 7 SDD)
- **관련 태스크**: Task 7 — 전역 키 제거(컷오버) + 에러 응답 매핑 확인

---

## 배경

T4~T6에서 모든 토스 콜사이트(payments, renewals, reconciliation, scheduler)를
`toss_provider.for_service(service)` 경유로 전환 완료. T7은 이제 전역 키/클라이언트를
완전히 제거해 "서비스별 키" 체계를 확정하는 컷오버 단계.

---

## 변경 내역

### 1. `app/main.py`
- `HttpTossClient` import 제거 (provider가 내부적으로 생성; main은 불필요)
- `own_toss` 변수 제거
- `app.state.toss = toss_client or HttpTossClient(...)` 라인 제거
- shutdown 블록의 `own_toss and isinstance(..., HttpTossClient)` aclose 블록 제거
- `toss_client` 파라미터는 유지 — provider override(테스트 Fake 주입용)

### 2. `app/core/deps.py`
- `get_toss` 함수 제거 (전역 `app.state.toss` 접근자)
- `TossClient` import 제거
- 모듈 docstring에 T7 컷오버 설명 추가

### 3. `app/api/deps.py`
- `get_toss` 재export 제거 (core/deps에서 삭제됐으므로)
- 주석으로 제거 이유 명시

### 4. `app/core/config.py`
- `toss_secret_key: str = ""` 필드 제거
- 모듈 docstring 갱신 (toss_secret_key 언급 제거, T7 컷오버 설명 추가)

### 5. `.env.dev` / `.env.prod`
- `TOSS_SECRET_KEY=...` 라인 제거 (gitignore 대상 — 커밋 안 됨)
- 주석으로 서비스별 등록 방법 안내

### 6. `.env.example`
- `TOSS_SECRET_KEY=test_sk_xxxx` 라인 제거
- 서비스별 어드민 콘솔 등록 안내 주석으로 교체

### 7. `app/api/v1/subscriptions.py`
- `get_toss` → `get_toss_provider` 교체
- `create_subscription`, `manual_pay` 핸들러: `toss_provider.for_service(service)` 호출로 전환

### 8. `app/api/v1/cards.py`
- `get_toss` → `get_toss_provider` 교체
- `register_card`, `delete_card` 핸들러: `toss_provider.for_service(service)` 호출로 전환

### 9. `app/api/v1/webhooks.py`
- `get_toss` → `get_toss_provider` 교체
- `toss_provider`를 `handle_webhook`에 전달 (내부에서 서비스별 해석)

### 10. `app/services/webhooks.py`
- `handle_webhook` 시그니처: `toss: TossClient` → `toss_provider: TossClientProvider`
- `_handle_payment_status_changed` 시그니처 동일하게 변경
- 내부에서 `payment.service_id`로 서비스 조회 → `for_service(service)` 해석
- `TossClient` import 제거, `TossClientProvider` / `TossKeyNotConfiguredError` 추가

### 11. `app/services/reconciliation.py`
- 데드 `else: for_service(None)` 분기 제거
  - `Payment.service_id`는 NON-NULLABLE → `if payment_service_id is not None:` 가드 불필요
  - 항상 `for_service(_svc)` 경로로 단순화

### 12. 테스트 파일 (T6 migration 누락 보완)
- `test_card_active.py`: `process_due` 호출 시 `TossClientProvider(override_client=fake)` 래핑
- `test_one_off_payment.py`: 동일
- `test_service_notifications.py`: 동일
- `test_trial_and_manual.py`: 동일 (예방적 수정)
- `test_webhooks.py`: `handle_webhook` 호출 시 `TossClientProvider(override_client=fake_toss)` 래핑

---

## 에러 응답 매핑 확인

`TossKeyNotConfiguredError`는 `DomainError` 서브클래스:
- `code = "TOSS_KEY_NOT_CONFIGURED"`
- `http_status = 422`

`app/api/errors.py`의 `DomainError` 핸들러가 `exc.http_status` / `exc.code` / `exc.message`를
그대로 JSON으로 반환 → 추가 핸들러 불필요. 기존 핸들러로 깔끔히 처리됨.

---

## 잔존 사용처 grep 결과

`grep -rnE "app\.state\.toss\b|get_toss\b|toss_secret_key|HttpTossClient" app --include="*.py" | grep -v provider | grep -v "_provider"` 실행 결과:

- `app/toss/client.py`: `HttpTossClient` 클래스 정의 — 유지 필요 (provider가 내부 생성)
- `app/core/errors.py`: `TossKeyNotConfiguredError` docstring — 정상
- `app/core/config.py`: 주석 — T7 컷오버 설명
- `app/main.py`: 주석 — T7 컷오버 설명
- `app/toss/fake.py`: 주석 — 참조용
- `app/services/registry.py`: `toss_secret_key` 파라미터 — 서비스 등록/수정 시 평문 수신 후 암호화 저장 (정상)
- `app/models/service.py`: `toss_secret_key_encrypted` 컬럼 — 서비스별 암호화 저장 (정상)
- `app/api/v1/payments.py`: 주석 — Task 5 설명
- `app/api/v1/cards.py`, `subscriptions.py`: 주석 — T7 컷오버 설명

**결론: 기능적 전역 toss 사용처 없음. 완전 제거 확인.**

---

## 전체 테스트 결과

```
623 passed in 61.27s (0:01:01)
```

전체 통과. 전역 키 부재 상태에서도 override(Fake) 경로로 모든 테스트 통과.

---

## 변경 파일 목록

```
app/main.py
app/core/deps.py
app/core/config.py
app/api/deps.py
app/api/v1/subscriptions.py
app/api/v1/cards.py
app/api/v1/webhooks.py
app/services/webhooks.py
app/services/reconciliation.py
.env.dev (gitignore)
.env.prod (gitignore)
.env.example
tests/integration/test_card_active.py
tests/integration/test_one_off_payment.py
tests/integration/test_service_notifications.py
tests/integration/test_trial_and_manual.py
tests/integration/test_webhooks.py
```

---

## Self-Review

- **완전성**: 전역 키 관련 모든 경로(lifespan·deps·config·.env·API라우터·서비스·정산) 제거.
- **안전성**: 테스트 주입 경로(`toss_client` 파라미터 → `override_client`)는 유지되어 테스트 인프라 무결.
- **에러 매핑**: `TossKeyNotConfiguredError` → 기존 DomainError 핸들러 그대로 처리 (추가 불필요).
- **우려사항**: 없음. `HttpTossClient` import는 `main.py`에서 제거됐고 `TossClient` 타입만 시그니처에 남아있어 추후 정리 가능하나 기능에 영향 없음.
