from app.toss.errors import TossError
from tests.factories import create_plan, create_service, create_subscription
from tests.helpers import api_request


async def _setup(db, cipher, **plan_kw):
    svc, api_key, secret = await create_service(db, cipher)
    plan = await create_plan(db, svc, **plan_kw)
    return svc, api_key, secret, plan


async def _register_card_api(client, api_key, secret, external_user_id, *,
                             customer_key, auth_key="auth-key"):
    """카드 등록 API 헬퍼 — Task 10: 구독 생성 전 카드 선등록 패턴.

    POST /api/v1/cards 를 호출해 카드를 먼저 등록한 뒤 구독을 생성한다.
    """
    resp = await api_request(client, "POST", "/api/v1/cards", api_key, secret,
                             json_body={"external_user_id": external_user_id,
                                        "customer_key": customer_key,
                                        "auth_key": auth_key})
    assert resp.status_code == 201, f"카드 등록 실패: {resp.status_code} {resp.text}"
    return resp


async def test_create_subscription_endpoint(client, db, cipher, fake_toss):
    # Task 10: 구독 전 카드 먼저 등록(POST /api/v1/cards) → auth_key/customer_key 구독 본문에서 제거
    svc, api_key, secret, plan = await _setup(db, cipher, price=12000)
    await _register_card_api(client, api_key, secret, "u-api-1",
                             customer_key="ck-api-1", auth_key="auth-from-sdk")
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-api-1",
                                        "plan_id": str(plan.id)})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["plan_name"] == plan.name
    assert body["card"]["issuerCode"] == "61"
    # 카드 등록 1회 + 구독 첫 결제 1회 = charges[0]은 구독 첫 결제
    assert fake_toss.charges[0]["amount"] == 12000


async def test_create_subscription_ignores_injected_amount(client, db, cipher, fake_toss):
    """본문에 amount를 넣어도 서버는 plan 가격으로 결제한다 (금액 조작 차단).

    Task 10: 카드 선등록 후 구독 생성. amount 필드는 스키마에 없으므로 무시된다.
    """
    svc, api_key, secret, plan = await _setup(db, cipher, price=50000)
    await _register_card_api(client, api_key, secret, "u-amt",
                             customer_key="ck-amt")
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-amt",
                                        "plan_id": str(plan.id),
                                        "amount": 1})
    assert resp.status_code == 201
    assert fake_toss.charges[0]["amount"] == 50000


async def test_duplicate_subscription_409(client, db, cipher):
    # Task 10: 카드 선등록 후 구독 중복 시도 — 카드는 1개 공유, 구독만 중복 검사
    svc, api_key, secret, plan = await _setup(db, cipher)
    await _register_card_api(client, api_key, secret, "u-dup",
                             customer_key="ck-dup")
    sub_body = {"external_user_id": "u-dup", "plan_id": str(plan.id)}
    first = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                              json_body=sub_body)
    assert first.status_code == 201
    second = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                               json_body=sub_body)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"


async def test_payment_failure_402_with_code(client, db, cipher, fake_toss):
    # Task 10: 카드 선등록 후 결제 실패 시나리오
    svc, api_key, secret, plan = await _setup(db, cipher)
    await _register_card_api(client, api_key, secret, "u-pf",
                             customer_key="ck-pf")
    fake_toss.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-pf",
                                        "plan_id": str(plan.id)})
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "INSUFFICIENT_FUNDS"


async def test_malformed_body_422_error_format(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-bad"})  # 필수 필드 누락
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_get_subscription_status(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-get")
    resp = await api_request(client, "GET", "/api/v1/subscriptions/u-get",
                             api_key, secret)
    assert resp.status_code == 200
    assert resp.json()["external_user_id"] == "u-get"
    missing = await api_request(client, "GET", "/api/v1/subscriptions/ghost",
                                api_key, secret)
    assert missing.status_code == 404


async def test_cancel_and_resume_endpoints(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cr")
    cancel = await api_request(client, "POST", "/api/v1/subscriptions/u-cr/cancel",
                               api_key, secret)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELED"
    resume = await api_request(client, "POST", "/api/v1/subscriptions/u-cr/resume",
                               api_key, secret)
    assert resume.status_code == 200
    assert resume.json()["status"] == "ACTIVE"


async def test_list_payments_endpoint(client, db, cipher, fake_toss):
    # Task 10: 카드 선등록 후 구독 생성 → 결제 내역 조회
    svc, api_key, secret, plan = await _setup(db, cipher, price=7000)
    await _register_card_api(client, api_key, secret, "u-pay",
                             customer_key="ck-pay")
    await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                      json_body={"external_user_id": "u-pay", "plan_id": str(plan.id)})
    resp = await api_request(client, "GET", "/api/v1/payments/u-pay", api_key, secret)
    assert resp.status_code == 200
    payments = resp.json()["payments"]
    assert len(payments) == 1
    assert payments[0]["amount"] == 7000
    assert payments[0]["status"] == "DONE"


async def test_list_payments_includes_cancel_fee(client, db, cipher, fake_toss):
    """결제 조회 응답에 취소 시 수수료/환불 예정액이 함께 반환된다."""
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType

    svc, api_key, secret = await create_service(db, cipher)
    svc.cancellation_fee_percent = 10  # 취소 수수료 10%
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u-cf",
                   order_id="cf-1", amount=10000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="cf-1", toss_payment_key="pay_cf",
                   requested_at=utcnow()))
    await db.commit()
    resp = await api_request(client, "GET", "/api/v1/payments/u-cf", api_key, secret)
    pay = resp.json()["payments"][0]
    assert pay["cancelable"] is True
    assert pay["cancel_fee_percent"] == 10
    assert pay["cancel_fee"] == 1000          # 10000 × 10%
    assert pay["cancel_refund_amount"] == 9000


async def test_cross_service_isolation(client, db, cipher):
    """서비스 A의 키로는 서비스 B의 구독을 볼 수 없다."""
    svc_a, key_a, secret_a = await create_service(db, cipher, name="svc-iso-a")
    svc_b, _, _ = await create_service(db, cipher, name="svc-iso-b")
    plan_b = await create_plan(db, svc_b)
    await create_subscription(db, cipher, svc_b, plan_b, external_user_id="u-iso")
    resp = await api_request(client, "GET", "/api/v1/subscriptions/u-iso",
                             key_a, secret_a)
    assert resp.status_code == 404  # A 범위에는 존재하지 않음


async def test_cross_service_mutation_isolation(client, db, cipher):
    """서비스 A 키로 서비스 B 사용자의 구독을 변경/취소할 수 없다 (전부 404)."""
    svc_a, key_a, secret_a = await create_service(db, cipher, name="mut-iso-a")
    svc_b, _, _ = await create_service(db, cipher, name="mut-iso-b")
    plan_b = await create_plan(db, svc_b)
    await create_subscription(db, cipher, svc_b, plan_b, external_user_id="u-bmut")

    for method, path in [("POST", "/api/v1/subscriptions/u-bmut/cancel"),
                         ("POST", "/api/v1/subscriptions/u-bmut/resume")]:
        resp = await api_request(client, method, path, key_a, secret_a)
        assert resp.status_code == 404
    cc = await api_request(client, "POST", "/api/v1/subscriptions/u-bmut/change-card",
                           key_a, secret_a,
                           json_body={"auth_key": "a", "customer_key": "ck-x"})
    assert cc.status_code == 404
    # 결제 이력도 빈 목록(타 서비스 결제 비노출)
    pays = await api_request(client, "GET", "/api/v1/payments/u-bmut", key_a, secret_a)
    assert pays.json()["payments"] == []


async def test_create_trial_subscription_api(client, db, cipher, fake_toss):
    # Task 10: 카드 선등록 후 체험 구독 생성 — auth_key/customer_key 구독 본문에서 제거
    svc, api_key, secret, plan = await _setup(db, cipher, price=12000,
                                              trial_enabled=True, trial_days=7)
    await _register_card_api(client, api_key, secret, "u-trial-api",
                             customer_key="ck-trial-api")
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-trial-api",
                                        "plan_id": str(plan.id), "trial": True})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "TRIAL"
    assert body["access_allowed"] is True
    # 체험 구독은 첫 결제 없음 — 카드 등록 시 빌링키 발급만 있고 charge 없음
    assert fake_toss.charges == []


async def test_access_allowed_flag_for_suspended(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-susp-api",
                              status="SUSPENDED", next_billing_at=None)
    resp = await api_request(client, "GET", "/api/v1/subscriptions/u-susp-api",
                             api_key, secret)
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUSPENDED"
    assert resp.json()["access_allowed"] is False


async def test_manual_pay_api_revives_suspended(client, db, cipher, fake_toss):
    """정지(SUSPENDED) 구독의 수동 결제 → ACTIVE 복귀.

    Task 10: 카드 선등록 후 create_subscription(factories)으로 SUSPENDED 구독 직접 삽입.
    카드(card_id)가 연결돼야 수동결제가 성공하므로 반드시 카드 먼저 등록한다.
    """
    from tests.factories import create_card
    svc, api_key, secret, plan = await _setup(db, cipher, price=9900)
    # 카드 등록 후 card_id를 구독에 연결 — 수동결제 시 카드 조회에 필요
    card = await create_card(db, fake_toss, cipher, svc, external_user_id="u-pay-api")
    await create_subscription(db, cipher, svc, plan, external_user_id="u-pay-api",
                              status="SUSPENDED", next_billing_at=None,
                              card_id=card.id)
    resp = await api_request(client, "POST", "/api/v1/subscriptions/u-pay-api/pay",
                             api_key, secret)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"
    assert resp.json()["access_allowed"] is True
    assert fake_toss.charges[0]["amount"] == 9900


async def test_add_days_endpoint(client, db, cipher, fake_toss):
    """외부 API: POST /subscriptions/{uid}/add-days → 200 + 만료일/다음결제 +N일."""
    from datetime import timedelta
    from app.core.clock import utcnow
    svc, api_key, secret, plan = await _setup(db, cipher, price=7000)
    base = utcnow().replace(microsecond=0)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-add",
                                    status="ACTIVE", period_end=base, next_billing_at=base)
    resp = await api_request(client, "POST", "/api/v1/subscriptions/u-add/add-days",
                             api_key, secret, json_body={"days": 15})
    assert resp.status_code == 200
    await db.refresh(sub)
    assert sub.current_period_end == base + timedelta(days=15)
    assert sub.next_billing_at == base + timedelta(days=15)


async def test_add_days_cross_service_404(client, db, cipher):
    """타 서비스 키로는 add-days 불가(404 — 범위 격리)."""
    svc_a, key_a, secret_a = await create_service(db, cipher, name="add-iso-a")
    svc_b, _, _ = await create_service(db, cipher, name="add-iso-b")
    plan_b = await create_plan(db, svc_b)
    await create_subscription(db, cipher, svc_b, plan_b, external_user_id="u-iso-b",
                              status="ACTIVE")
    resp = await api_request(client, "POST", "/api/v1/subscriptions/u-iso-b/add-days",
                             key_a, secret_a, json_body={"days": 10})
    assert resp.status_code == 404


async def test_plan_response_exposes_cycle_minutes(db, cipher):
    """Task 5: PlanResponse가 MINUTE 주기 요금제의 cycle_minutes를 노출한다."""
    from app.schemas.api import PlanResponse
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, billing_cycle="MINUTE", cycle_minutes=5)
    resp = PlanResponse.from_model(plan)
    assert resp.billing_cycle == "MINUTE"
    assert resp.cycle_minutes == 5
