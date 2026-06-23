import uuid

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.core.security import (
    generate_hmac_secret,
    generate_service_api_key,
    hash_password,
    sha256_hex,
)
from app.models import Card, Plan, Service, Subscription, User  # Task 9: Card 직접 삽입 헬퍼용
from app.services.billing_math import compute_period_end
from app.services.cards import register_or_replace_card  # Task 7: 카드 등록 헬퍼


async def create_service(db, cipher: AesGcmCipher, *, name=None,
                         allowed_ips=None, manager_email=None):
    """반환: (Service, api_key 평문, hmac_secret 평문)"""
    name = name or f"svc-{uuid.uuid4().hex[:8]}"
    api_key = generate_service_api_key()
    secret = generate_hmac_secret()
    svc = Service(
        name=name,
        allowed_ips=allowed_ips if allowed_ips is not None else ["127.0.0.1"],
        manager_email=manager_email or f"{name}@medisolveai.com",
        api_key_hash=sha256_hex(api_key),
        api_key_encrypted=cipher.encrypt(api_key),
        hmac_secret_encrypted=cipher.encrypt(secret),
    )
    db.add(svc)
    await db.commit()
    return svc, api_key, secret


async def create_plan(db, service, *, name="기본 요금제", price=10000,
                      billing_cycle="MONTH", cycle_days=None, cycle_minutes=None,  # cycle_minutes: MINUTE 주기 요금제용
                      first_payment_type="NONE", first_payment_value=None,
                      recurring_discount_type="NONE", recurring_discount_value=None,
                      status="ACTIVE", trial_enabled=False, trial_days=None,
                      auto_renew=True, extra_info=None):  # 자동결제 여부·추가정보(요청 013)
    plan = Plan(service_id=service.id, name=name, price=price,
                billing_cycle=billing_cycle, cycle_days=cycle_days,
                cycle_minutes=cycle_minutes,                        # MINUTE 주기일 때 실제 분 수(Task 2)
                first_payment_type=first_payment_type,
                first_payment_value=first_payment_value,
                recurring_discount_type=recurring_discount_type,
                recurring_discount_value=recurring_discount_value, status=status,
                trial_enabled=trial_enabled, trial_days=trial_days,
                auto_renew=auto_renew,                              # 자동결제 여부(요청 013)
                extra_info=extra_info if extra_info is not None else {})  # 추가정보(요청 013)
    db.add(plan)
    await db.commit()
    return plan


_UNSET = object()  # next_billing_at=None(NULL)과 '미지정'을 구분하는 센티널


async def create_card(db, toss, cipher, service, *, external_user_id="user-1",
                      customer_key="ck-valid-1", auth_key="auth-1"):
    """테스트용 카드 등록 헬퍼 — register_or_replace_card 래퍼.

    Task 7: 구독 생성 전 카드를 반드시 등록해야 하므로, 테스트에서 편리하게
    호출할 수 있도록 factories에 추가한다.

    Args:
        db: AsyncSession.
        toss: FakeTossClient 인스턴스.
        cipher: 암호화 인스턴스.
        service: 카드가 속할 Service 인스턴스.
        external_user_id: 외부 사용자 식별자(기본 "user-1").
        customer_key: 토스 customerKey(기본 "ck-valid-1").
        auth_key: 토스 authKey(기본 "auth-1").

    Returns:
        등록된 Card 인스턴스.
    """
    return await register_or_replace_card(
        db, toss, cipher,
        service=service,
        external_user_id=external_user_id,
        customer_key=customer_key,
        auth_key=auth_key,
    )


async def create_card_direct(db, cipher: AesGcmCipher, service, *,
                             external_user_id: str = "user-1",
                             billing_key: str,
                             customer_key: str = "ck-direct") -> Card:
    """테스트에서 특정 빌링키 값을 가진 Card 행을 직접 삽입한다.

    Task 9: BILLING_DELETED 웹훅 테스트는 페이로드의 billingKey 값이
    DB의 Card.billing_key_hash와 정확히 일치해야 한다. create_card()는
    FakeTossClient가 자동 생성하는 빌링키 값을 사용하므로, 테스트에서 특정
    billingKey("bk_hooked" 등)를 직접 심으려면 이 헬퍼를 사용한다.

    Args:
        db: AsyncSession.
        cipher: AES-GCM 암호화 인스턴스(빌링키 암호화에 사용).
        service: Card가 속할 Service 인스턴스.
        external_user_id: 외부 사용자 식별자.
        billing_key: DB에 심을 토스 빌링키 평문 값.
        customer_key: 토스 customerKey(기본 "ck-direct").

    Returns:
        삽입된 Card 인스턴스.
    """
    # 빌링키를 암호화하고 SHA-256 해시도 계산해 DB에 삽입 — 서비스 레이어와 동일한 방식
    card = Card(
        service_id=service.id,
        external_user_id=external_user_id,
        customer_key=customer_key,
        billing_key_encrypted=cipher.encrypt(billing_key),  # AES-GCM 암호화 보관
        billing_key_hash=sha256_hex(billing_key),            # 웹훅 조회용 해시
    )
    db.add(card)
    await db.commit()
    return card


async def create_subscription(db, cipher, service, plan, *, external_user_id="user-1",
                              status="ACTIVE", retry_count=0,
                              period_start=None, period_end=None,
                              next_billing_at=_UNSET,
                              card_id=None):  # cards.id FK — Task 5 이후 필수(NOT NULL)
    """테스트용 Subscription 행 직접 삽입.

    카드 보관함(Task 4~) 이후 Subscription은 billing_key·customer_key 없이
    card_id FK만 보유한다. card_id=None으로 호출 시 DB NOT NULL 제약 위반이
    발생할 수 있으므로, 반드시 사전에 등록된 Card.id를 전달해야 한다.

    Args:
        db: AsyncSession.
        cipher: 암호화 인스턴스 — 현재 미사용(시그니처 호환성 유지).
        service: 구독이 속할 Service 인스턴스.
        plan: 구독할 Plan 인스턴스.
        external_user_id: 외부 사용자 식별자.
        status: 초기 구독 상태(기본 ACTIVE).
        retry_count: PAST_DUE 재시도 횟수.
        period_start: 현재 주기 시작 시각(UTC); 기본 현재 시각.
        period_end: 현재 주기 종료 시각(UTC); 기본 plan 주기 계산.
        next_billing_at: 다음 자동결제 예정 시각; 기본 period_end.
        card_id: cards 테이블의 UUID — NOT NULL 컬럼이므로 반드시 전달.
    """
    start = period_start or utcnow()
    end = period_end or compute_period_end(start, plan.billing_cycle, plan.cycle_days)
    # Subscription은 카드 보관함 이전 빌링키·카드정보 컬럼이 제거됐으므로
    # card_id FK만 설정한다 (billing_key_encrypted 등은 cards 테이블로 이동)
    sub = Subscription(
        service_id=service.id,
        plan_id=plan.id,
        external_user_id=external_user_id,
        card_id=card_id,  # cards.id NOT NULL FK
        status=status,
        current_period_start=start,
        current_period_end=end,
        next_billing_at=end if next_billing_at is _UNSET else next_billing_at,
        retry_count=retry_count,
    )
    db.add(sub)
    await db.commit()
    return sub


async def create_user(db, *, email=None, password="Password123!", role="SYSTEM_ADMIN",
                      service_id=None, status="ACTIVE"):
    """반환: (User, password 평문)"""
    user = User(email=email or f"u-{uuid.uuid4().hex[:8]}@medisolveai.com",
                password_hash=hash_password(password), role=role,
                service_id=service_id, status=status)
    db.add(user)
    await db.commit()
    return user, password
