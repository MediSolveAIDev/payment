"""개발용 데모 데이터 시드 — 대시보드/목록 화면을 채워 디자인을 확인하기 위함.

실행: uv run python scripts/seed_demo.py
운영 DB에는 절대 실행하지 마세요(서비스명 'DEMO-*'만 추가/정리).
"""

import asyncio
import random
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dateutil.relativedelta import relativedelta  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.core.clock import utcnow  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.crypto import AesGcmCipher  # noqa: E402
from app.core.db import create_engine, create_session_factory  # noqa: E402
from app.core.security import (  # noqa: E402
    generate_hmac_secret,
    generate_service_api_key,
    sha256_hex,
)
from app.models import Payment, Plan, Service, Subscription  # noqa: E402

random.seed(7)


async def main() -> None:
    # 환경별 로딩 사용(.env + .env.<APP_ENV>). 기본 dev. 운영 시드는 APP_ENV=prod로 실행.
    settings = Settings()
    cipher = AesGcmCipher(settings.encryption_key)
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    now = utcnow()

    async with factory() as db:
        # 기존 DEMO 데이터 정리 (payments/subscriptions/plans → services 순)
        demo_ids = (await db.scalars(select(Service.id).where(Service.name.like("DEMO-%")))).all()
        if demo_ids:
            sub_ids = (await db.scalars(select(Subscription.id).where(
                Subscription.service_id.in_(demo_ids)))).all()
            if sub_ids:
                await db.execute(delete(Payment).where(Payment.subscription_id.in_(sub_ids)))
                await db.execute(delete(Subscription).where(Subscription.id.in_(sub_ids)))
            await db.execute(delete(Plan).where(Plan.service_id.in_(demo_ids)))
            await db.execute(delete(Service).where(Service.id.in_(demo_ids)))
            await db.commit()

        services = []
        for name in ["DEMO-mediness", "DEMO-say", "DEMO-bay"]:
            svc = Service(name=name, allowed_ips=["127.0.0.1"],
                          manager_email=f"{name.lower()}@medisolveai.com",
                          api_key_hash=sha256_hex(generate_service_api_key()),
                          hmac_secret_encrypted=cipher.encrypt(generate_hmac_secret()))
            db.add(svc)
            services.append(svc)
        await db.flush()

        plans = []
        for svc in services:
            for pname, price in [("베이직", 9900), ("프로", 29000), ("엔터프라이즈", 99000)]:
                plan = Plan(service_id=svc.id, name=pname, price=price, billing_cycle="MONTH")
                db.add(plan)
                plans.append(plan)
        await db.flush()

        statuses = (["ACTIVE"] * 11 + ["PAST_DUE"] * 3 + ["CANCELED"] * 3 + ["EXPIRED"] * 4)
        n_sub = 0
        for _ in range(34):
            plan = random.choice(plans)
            status = random.choice(statuses)
            created = now - timedelta(days=random.randint(0, 330))
            start = created
            # ACTIVE/PAST_DUE는 만기일·다음결제를 미래로 둬 스케줄러 churn을 막는다(데모 안정화)
            if status in ("ACTIVE", "PAST_DUE"):
                end = now + timedelta(days=random.randint(5, 27))
                nxt = end
            else:
                end = start + relativedelta(months=1)
                nxt = None
            sub = Subscription(
                service_id=plan.service_id, plan_id=plan.id,
                external_user_id=f"user-{1000 + n_sub}", customer_key=f"ck-{uuid.uuid4()}",
                billing_key_encrypted=cipher.encrypt(f"bk_{n_sub}"),
                billing_key_hash=sha256_hex(f"bk_{n_sub}"),
                card_info={"number": "1234-****-****-5678", "issuerCode": "61"},
                status=status, current_period_start=start, current_period_end=end,
                next_billing_at=nxt, created_at=created)
            db.add(sub)
            await db.flush()
            n_sub += 1

            # 가입월부터 매월 결제 이력 — 대부분 성공, 가끔 실패
            months = random.randint(1, 8)
            for m in range(months):
                approved = created + relativedelta(months=m)
                if approved > now:
                    break
                failed = random.random() < 0.12
                pay = Payment(
                    subscription_id=sub.id, order_id=f"demo-{uuid.uuid4().hex[:16]}",
                    toss_payment_key=None if failed else f"pay_{uuid.uuid4().hex[:12]}",
                    amount=plan.price, payment_type="FIRST" if m == 0 else "RENEWAL",
                    status="FAILED" if failed else "DONE",
                    failure_code="INSUFFICIENT_FUNDS" if failed else None,
                    failure_message="잔액 부족" if failed else None,
                    idempotency_key=f"seed-{uuid.uuid4()}",
                    requested_at=approved,
                    approved_at=None if failed else approved)
                db.add(pay)
        await db.commit()
        print(f"시드 완료: 서비스 {len(services)} · 요금제 {len(plans)} · 구독 {n_sub}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
