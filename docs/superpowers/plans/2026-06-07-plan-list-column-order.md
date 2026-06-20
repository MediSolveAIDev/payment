# 요금제 리스트 컬럼 순서 변경 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요금제 리스트 2곳의 컬럼을 "이름|정가|체험|첫구독 할인|첫 결제액|상시할인|정기 결제액|주기|상태" 순서로 통일하고, 전역 리스트에 체험 컬럼 추가 + 첫구독 할인 한글화.

**Architecture:** 순수 템플릿 변경 — 라우트/모델/계산 무변경. 셀 마크업(title 툴팁·스타일)은 이동만.

**Tech Stack:** Jinja2, pytest

**스펙:** `docs/superpowers/specs/2026-06-07-plan-list-column-order-design.md`

---

### Task 1: 컬럼 순서 재배치 + 전역 리스트 보강

**Files:**
- Modify: `app/admin/templates/services/_plans_table.html` (thead/tbody 순서)
- Modify: `app/admin/templates/plans/_table.html` (순서 + 체험 컬럼 + 한글화 + colspan 11)
- Test: `tests/e2e/test_service_detail_page.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_service_detail_page.py`에 추가:

```python
async def test_plan_table_column_order(client, db, redis_client, cipher):
    """컬럼 순서: 이름|정가|체험|첫구독 할인|첫 결제액|상시할인|정기 결제액|주기|상태."""
    svc, _, _ = await create_service(db, cipher, name="col-order-svc")
    await create_plan(db, svc, name="col-order-plan", price=10000,
                      trial_enabled=True, trial_days=14,
                      first_payment_type="DISCOUNT_AMOUNT", first_payment_value=1000)
    await _admin(client, db, redis_client)
    detail = (await client.get(f"/admin/services/{svc.id}")).text
    thead = detail[detail.index("요금제 관리"):detail.index("</thead>",
                                                       detail.index("요금제 관리"))]
    order = ["이름", "정가", "체험", "첫구독 할인", "첫 결제액",
             "상시할인", "정기 결제액", "주기(반복회차)", "상태"]
    idx = [thead.index(c) for c in order]
    assert idx == sorted(idx), f"컬럼 순서 불일치: {order}"
    # 전역 리스트: 체험 컬럼 + 한글 첫구독 할인 표기
    plans_page = (await client.get("/admin/plans")).text
    assert "체험" in plans_page and "14일" in plans_page
    assert "1,000원 할인" in plans_page
    assert "DISCOUNT_AMOUNT 1000" not in plans_page  # 영문 enum 표기 제거
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/e2e/test_service_detail_page.py::test_plan_table_column_order -v`
Expected: FAIL — 상세 thead 순서 불일치(체험이 주기 뒤), 전역 리스트에 "체험"/한글 표기 없음

- [ ] **Step 3: 상세 테이블 재배치** — `services/_plans_table.html`:

thead를:

```html
    <thead><tr><th>이름</th><th>정가</th><th>체험</th><th>첫구독 할인</th><th>첫 결제액</th><th>상시할인</th><th>정기 결제액</th><th>주기(반복회차)</th><th>상태</th><th></th></tr></thead>
```

tbody의 `<td>`들을 같은 순서로 재배치 (셀 마크업 불변 — 이동만):

```html
        <td style="font-weight:500">{{ plan.name }}</td>
        <td {% if plan.recurring_amount != plan.price %}style="text-decoration:line-through;color:var(--black-40)"{% else %}style="font-weight:600"{% endif %}>{{ "{:,}".format(plan.price) }}원</td>
        <td class="muted">{% if plan.trial_enabled %}{{ plan.trial_days }}일{% else %}-{% endif %}</td>
        <td class="muted">
          {%- if plan.first_payment_type == 'NONE' -%}없음
          {%- elif plan.first_payment_type == 'FREE' -%}무료
          {%- elif plan.first_payment_type == 'DISCOUNT_AMOUNT' -%}{{ "{:,}".format(plan.first_payment_value) }}원 할인
          {%- elif plan.first_payment_type == 'DISCOUNT_PERCENT' -%}{{ plan.first_payment_value }}% 할인{%- endif -%}
        </td>
        <td title="{{ plan.first_tooltip }}" {% if plan.first_amount == plan.recurring_amount %}class="muted"{% else %}style="font-weight:600"{% endif %}>{{ "{:,}".format(plan.first_amount) }}원</td>
        <td class="muted">
          {%- if plan.recurring_discount_type == 'DISCOUNT_PERCENT' -%}{{ plan.recurring_discount_value }}%
          {%- elif plan.recurring_discount_type == 'DISCOUNT_AMOUNT' -%}{{ "{:,}".format(plan.recurring_discount_value) }}원
          {%- else -%}−{%- endif -%}
        </td>
        <td title="{{ plan.recurring_tooltip }}" style="font-weight:600">{{ "{:,}".format(plan.recurring_amount) }}원</td>
        <td>{{ plan.billing_cycle }}{% if plan.cycle_days %} {{ plan.cycle_days }}일{% endif %}</td>
        <td><span class="badge badge-{{ plan.status }}">{{ plan.status }}</span></td>
```

(마지막 액션 `<td style="white-space:nowrap">…</td>`는 그대로. 빈 행 colspan 10 유지.)

- [ ] **Step 4: 전역 리스트 재배치+보강** — `plans/_table.html`:

thead를:

```html
  <thead><tr>
    <th>서비스</th>
    {{ L.sort_th(pp, '/admin/plans', 'name', '이름', target='list-plans') }}
    {{ L.sort_th(pp, '/admin/plans', 'price', '정가', target='list-plans') }}
    <th>체험</th>
    <th>첫구독 할인</th>
    <th>첫 결제액</th>
    <th>상시할인</th>
    <th>정기 결제액</th>
    <th>주기</th>
    {{ L.sort_th(pp, '/admin/plans', 'status', '상태', target='list-plans') }}
    <th></th>
  </tr></thead>
```

tbody의 서비스/이름/정가 셀 다음을 (기존 `첫구독 혜택` 영문 enum 셀은 삭제하고 한글 분기로 교체):

```html
      <td class="muted">{% if plan.trial_enabled %}{{ plan.trial_days }}일{% else %}-{% endif %}</td>
      <td class="muted">
        {%- if plan.first_payment_type == 'NONE' -%}없음
        {%- elif plan.first_payment_type == 'FREE' -%}무료
        {%- elif plan.first_payment_type == 'DISCOUNT_AMOUNT' -%}{{ "{:,}".format(plan.first_payment_value) }}원 할인
        {%- elif plan.first_payment_type == 'DISCOUNT_PERCENT' -%}{{ plan.first_payment_value }}% 할인{%- endif -%}
      </td>
      <td title="{{ plan.first_tooltip }}" {% if plan.first_amount == plan.recurring_amount %}class="muted"{% else %}style="font-weight:600"{% endif %}>{{ "{:,}".format(plan.first_amount) }}원</td>
      <td class="muted">
        {%- if plan.recurring_discount_type == 'DISCOUNT_PERCENT' -%}{{ plan.recurring_discount_value }}%
        {%- elif plan.recurring_discount_type == 'DISCOUNT_AMOUNT' -%}{{ "{:,}".format(plan.recurring_discount_value) }}원
        {%- else -%}−{%- endif -%}
      </td>
      <td title="{{ plan.recurring_tooltip }}" style="font-weight:600">{{ "{:,}".format(plan.recurring_amount) }}원</td>
      <td>{{ plan.billing_cycle }}{% if plan.cycle_days %}({{ plan.cycle_days }}일){% endif %}</td>
```

(상태 배지 셀·액션 셀 그대로.) 빈 행 `colspan="10"` → `colspan="11"`.

- [ ] **Step 5: 통과 + 회귀 확인**

Run: `pytest tests/e2e/test_service_detail_page.py tests/e2e/test_htmx_partials.py -v && pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add app/admin/templates/services/_plans_table.html app/admin/templates/plans/_table.html tests/e2e/test_service_detail_page.py
git commit -m "feat(plans): 요금제 리스트 컬럼 순서 통일 + 전역 체험/한글 표기"
```
