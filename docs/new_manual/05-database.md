# 05. 데이터베이스와 마이그레이션

> 목표: 테이블 구조를 파악하고, 컬럼 하나를 안전하게 추가할 수 있다.

## 1. 테이블 한 줄 요약 (8개)

| 테이블 | 역할 | 꼭 알아야 할 것 |
|---|---|---|
| services | 테넌트(사내 서비스) | API키는 SHA-256 해시로 대조, 평문은 AES 암호문으로 별도 보관 |
| plans | 요금제 | 결제 주기(billing_cycle/cycle_days)는 생성 후 불변 |
| subscriptions | 구독 | **부분 유니크** `uq_subscriptions_one_per_user` — EXPIRED 제외 (서비스,사용자)당 1개 |
| payments | 결제 원장(불변, 삭제 금지) | `order_id`는 서비스 내 고유, **토스 전달용은 `toss_order_id`(전역 고유)** |
| users / user_services | 어드민 계정 / 담당 서비스 N:M | 역할: SYSTEM_ADMIN / SERVICE_MANAGER |
| audit_logs | 감사 이력(append-only) | 모든 중요 행위가 여기 남는다 — 디버깅 1차 도구 |
| webhook_events | 웹훅 멱등 기록 | transmission-id 유니크 |
| global_settings | 전역설정 단일 행(id=1) | 재시도 정책·킬스위치·어드민 IP |

돈 컬럼은 전부 BigInteger(정수 KRW), 시각은 전부 timezone-aware UTC.

## 2. 성능을 위해 존재하는 인덱스 (지우면 안 됨)

- `ix_subscriptions_due (status, next_billing_at)` — 5분 배치의 결제 대상 조회
- `ix_payments_status_requested` — 정산 스윕(5분마다)
- `ix_subscriptions_service_id`, `ix_subscriptions_status_period_end`,
  `ix_payments_service_approved`, `ix_audit_logs_created_at`, `ix_audit_logs_target`
  — 어드민 목록·대시보드·만료 스윕용 (성능 감사 Phase 3에서 추가)

## 3. 마이그레이션 절차 (이 프로젝트는 **수동 작성** 방식)

```bash
ls alembic/versions/        # 최신 리비전 확인 — 새 파일의 down_revision으로 쓴다
```

1. `app/models/…`에 컬럼/인덱스 추가 (주석 필수)
2. `alembic/versions/`의 **최근 파일을 복사**해 새 리비전 작성 — docstring에 "왜",
   `revision`/`down_revision` 체인 정확히, `upgrade()`와 `downgrade()` 모두 구현
3. `uv run alembic upgrade head` (dev DB 적용) → `uv run pytest` (테스트 DB는
   create_all이라 모델만 맞으면 됨 — 모델과 마이그레이션이 어긋나면 여기서 안 잡히니 주의)
4. 기존 데이터가 있으면 백필(`op.execute("UPDATE …")`)을 마이그레이션 안에서 처리
   — `a7b8c9d0e1f2_payment_order_scope.py`가 좋은 예시(컬럼 추가→백필→NOT NULL→제약 교체)

**운영 반영 시**: 인덱스 생성은 잠금을 유발할 수 있다 — 트래픽 적은 시간에 `alembic upgrade head`.

> 더 깊이: [dev_manual 02장 — 컬럼 단위 상세](../dev_manual/manual.html)
