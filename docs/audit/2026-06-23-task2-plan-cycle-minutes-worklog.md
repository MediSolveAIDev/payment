# 워크로그: Task 2 — plans.cycle_minutes 컬럼 + alembic 마이그레이션 + 팩토리

**날짜**: 2026-06-23
**작업자**: Claude (subagent-driven-development Task 2)

## 작업 개요

Task 1(BillingCycle.MINUTE enum + compute_period_end cycle_minutes 파라미터) 완료에 이어,
데이터 계층으로 `plans` 테이블에 `cycle_minutes` nullable Integer 컬럼을 추가하고
alembic 마이그레이션을 작성·적용했다. 테스트 팩토리도 이 필드를 받도록 확장했다.

## 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/models/plan.py` | `cycle_days` 아래 `cycle_minutes: Mapped[int | None]` 컬럼 추가 |
| `alembic/versions/e1f2a3b4c5d7_plan_cycle_minutes.py` | 신규 마이그레이션 파일 생성 |
| `tests/factories.py` | `create_plan` 시그니처·Plan() 생성자에 `cycle_minutes=None` 추가 |
| `docs/user_manual/04-admin-plan.md` | 결제 주기 표/설명에 "분" 항목 추가 |
| `docs/user_manual/04-admin-plan.html` | 동일 변경 HTML 반영 |

## alembic 실행 결과

```
$ DATABASE_URL=... uv run alembic upgrade head
INFO  Running upgrade d3e4f5a6b7c8 -> e1f2a3b4c5d7, plans.cycle_minutes 컬럼 추가 — MINUTE 주기 요금제의 분 수 보관

$ DATABASE_URL=... uv run alembic current
e1f2a3b4c5d7 (head)
```

## 테스트 결과

- `uv run pytest -q tests/unit/test_billing_math.py tests/unit/test_billing_math_edges.py tests/integration/test_models.py`: **41 passed, 4 errors(teardown Redis 연결 오류 — 기존 인프라 문제, 변경과 무관)**
- 전체 회귀: 265 failed, 343 passed, 503 errors — 실패/오류 모두 Redis(6380 미기동) 관련, 이번 변경과 무관

## 참고 사항

- 환경: payment-postgres 컨테이너(localhost:5432)가 중지되어 있어 시작 후 진행
- downgrade는 `op.drop_column("plans", "cycle_minutes")` 확인 완료
- 검증/서비스 로직은 건드리지 않음(Task 3 범위)
