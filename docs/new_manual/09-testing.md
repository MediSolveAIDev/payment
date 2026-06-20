# 09. 테스트 — 안전망 사용법

> 목표: 변경 후 무엇을 돌려야 하는지, 새 테스트를 어디에 어떻게 쓰는지 안다.
> 이 프로젝트의 1차 안전망은 **실제 Postgres/Redis + 가짜 토스**로 도는 통합 테스트다.

## 1. 구조

| 디렉터리 | 무엇 | 예시 |
|---|---|---|
| tests/unit | DB 없는 순수 로직 | billing_math, transitions(상태머신 규칙), XFF 파싱 |
| tests/integration | 서비스 함수 + 실제 DB/Redis | **test_renewals.py(갱신 상태머신 — 가장 중요)**, 구독 생성/관리, 정산, 웹훅 |
| tests/e2e | HTTP로 화면/API 전체 | 어드민 화면, htmx, 엑셀, 외부 API |
| tests/security | 인증 전용 | HMAC 6단계, 어드민 보안 |

## 2. 인프라가 어떻게 도는가 (tests/conftest.py)

- `docker compose up -d`의 같은 컨테이너를 쓰되 **별도 DB(payment_test)·Redis DB 15** 사용
- 세션 시작 시 `drop_all → create_all`(마이그레이션 미사용 — 모델이 곧 스키마),
  **테스트마다 TRUNCATE + Redis flush** — 테스트 간 독립
- ⚠️ **같은 머신에서 pytest 두 개를 동시에 돌리지 마라** — 서로의 스키마를 drop해서
  수십 건의 가짜 실패(IntegrityError 등)가 난다. 진짜 실패인지 헷갈리면 **단독으로 재실행**이 1순위.

## 3. FakeTossClient — 결제 테스트의 핵심 (app/toss/fake.py)

실제 토스 대신 주입되는 가짜. 시나리오 주입이 가능하다:

```python
fake = FakeTossClient()
fake.fail_charge_with = TossError("REJECT_CARD", "카드 거절")   # 다음 결제를 거절
fake.fail_charge_with = TossTimeoutError()                      # 타임아웃(결과불명) 시뮬레이션
fake.payments_by_order[toss_order_id] = FakeTossClient._result_for(toss_order_id, 5000)
# ↑ "토스에는 사실 승인돼 있었다" 상태를 만들어 정산 스윕 수렴을 테스트
fake.charges          # 토스에 도달한 청구 기록(금액·멱등키 검증용)
```

멱등키 재생(같은 키 재요청 시 같은 결과 반환)까지 재현하므로,
**이중결제 방어 테스트는 fake.charges 길이로 검증**하는 패턴을 쓴다.

## 4. 자주 쓰는 명령

```bash
uv run pytest -q                          # 전체 (≈50초)
uv run pytest tests/integration/test_renewals.py -q     # 갱신 상태머신만
uv run pytest -k "manual" -q              # 이름 매칭
uv run pytest --lf -x                     # 직전 실패만, 첫 실패에서 중단
```

테스트 후 `docs/test_report/`에 HTML 리포트가 자동 생성된다(conftest 플러그인).

## 5. 새 테스트를 쓸 때

- 픽스처: `db`(세션) `cipher` `redis_client` `client`(HTTP) + `tests/factories.py`
  (create_service/plan/subscription/user)부터 시작
- 시간 의존 로직은 `process_due(..., now=특정시각)`처럼 **시간을 주입** — sleep 금지
- 갱신/결제 테스트는 기존 `test_renewals.py`의 시나리오 스타일(주석으로 타임라인 서술)을 모방
