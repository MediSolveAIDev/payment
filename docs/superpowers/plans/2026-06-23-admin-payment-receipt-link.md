# 어드민 결제 목록 매출전표 링크 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 어드민 결제 목록 각 행에 저장된 토스 `receipt.url`로 가는 매출전표 링크를 추가한다.

**Architecture:** `Payment.raw_response["receipt"]["url"]`을 안전하게 읽는 헬퍼 `receipt_url`을 추가하고 Jinja 템플릿 전역으로 등록(`payment_status_ko`와 동일 방식), `payments/list.html`에 "매출전표" 열을 추가해 URL이 있으면 새 탭 링크, 없으면 `-`를 렌더한다. 추가 토스 호출·엔드포인트·스키마 변경 없음.

**Tech Stack:** FastAPI, Jinja2(htmx 어드민), pytest.

## Global Constraints

- URL 출처는 저장된 `Payment.raw_response["receipt"]["url"]`만 — 토스 호출/새 엔드포인트/스키마 변경 없음.
- receipt URL이 없으면 `-` 표시(graceful). 링크는 `target="_blank" rel="noopener"`.
- 범위는 결제 **목록**만(상세 제외).
- `raw_response`는 어드민 화면 전용(외부 API 미노출 — 기존 정책 유지).
- 한국어 주석. 기존 패턴(`app/admin/__init__.py` 템플릿 전역 등록) 준수.
- 테스트 인프라: DB localhost:5432, Redis localhost:6380.

---

### Task 1: receipt_url 헬퍼 + 템플릿 전역 등록 + 목록 링크

**Files:**
- Modify: `app/admin/__init__.py` (헬퍼 정의 + `templates.env.globals["receipt_url"]` 등록)
- Modify: `app/admin/templates/payments/list.html` (헤더 `<th>매출전표</th>` + 셀 + colspan 9→10)
- Test: `tests/unit/test_admin_helpers.py` (헬퍼 단위 테스트 — 기존 파일)

**Interfaces:**
- Produces: `receipt_url(payment) -> str | None` — `payment.raw_response`가 dict이고 `receipt.url`(str)이면 그 값, 아니면 None.

- [ ] **Step 1: 실패 테스트 작성** — `tests/unit/test_admin_helpers.py`에 추가 (기존 import 스타일 사용)

```python
from types import SimpleNamespace
from app.admin import receipt_url


def _pay(raw):
    return SimpleNamespace(raw_response=raw)


def test_receipt_url_present():
    assert receipt_url(_pay({"receipt": {"url": "https://dashboard.tosspayments.com/receipt/abc"}})) \
        == "https://dashboard.tosspayments.com/receipt/abc"


def test_receipt_url_missing_receipt():
    assert receipt_url(_pay({"approvedAt": "2026-06-23"})) is None


def test_receipt_url_receipt_without_url():
    assert receipt_url(_pay({"receipt": {}})) is None


def test_receipt_url_none_raw():
    assert receipt_url(_pay(None)) is None


def test_receipt_url_non_string_url():
    # url 값이 문자열이 아니면 None (방어)
    assert receipt_url(_pay({"receipt": {"url": 123}})) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_admin_helpers.py -k receipt_url -v`
Expected: FAIL (ImportError: receipt_url 없음)

- [ ] **Step 3: 헬퍼 구현 + 등록** — `app/admin/__init__.py` (다른 전역 등록부 근처)

```python
def receipt_url(payment) -> str | None:
    """결제의 토스 매출전표(영수증) URL을 반환한다(없으면 None).

    승인 시 저장한 raw_response의 receipt.url을 안전하게 읽는다.
    카드 결제(DONE)면 보통 존재하고, 실패·대기·과거 미보유 건은 None.
    """
    raw = getattr(payment, "raw_response", None)
    if not isinstance(raw, dict):
        return None
    receipt = raw.get("receipt")
    if not isinstance(receipt, dict):
        return None
    url = receipt.get("url")
    return url if isinstance(url, str) and url else None


# 매출전표(영수증) 링크. 사용: {{ receipt_url(p) }} (어드민 결제 목록)
templates.env.globals["receipt_url"] = receipt_url
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_admin_helpers.py -k receipt_url -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 목록 템플릿에 열 추가** — `app/admin/templates/payments/list.html`

헤더 `<thead>`의 마지막 `<th>`(요청·시각 헤더) 뒤에 추가:
```html
    <th>매출전표</th>
```
행에서 `requested_at` 셀(`<td class="muted">{{ p.requested_at|kst(...) }}</td>`) 바로 뒤에 추가:
```html
      <td class="muted">{% set rurl = receipt_url(p) %}{% if rurl %}<a href="{{ rurl }}" target="_blank" rel="noopener">매출전표</a>{% else %}-{% endif %}</td>
```
빈 목록 행 `colspan`을 9에서 10으로:
```html
    <tr><td colspan="10" class="muted">결제 이력이 없습니다</td></tr>
```

- [ ] **Step 6: 목록 렌더 회귀 확인**

Run: `uv run pytest tests/e2e/test_admin_operations.py -q`
Expected: PASS (결제 목록 렌더 깨지지 않음)

- [ ] **Step 7: 커밋**

```bash
git add app/admin/__init__.py app/admin/templates/payments/list.html tests/unit/test_admin_helpers.py
git commit -m "feat: 어드민 결제 목록에 매출전표 링크(raw_response.receipt.url)"
```

---

### Task 2: 목록 렌더 e2e + 문서/워크로그

**Files:**
- Test: `tests/e2e/` (결제 목록 렌더 테스트가 있는 파일 — `grep -rl "/admin/payments" tests/e2e`로 확인; 없으면 가장 근접한 결제목록 e2e에 추가)
- Modify: 어드민 콘솔 매뉴얼 결제 목록 설명 .md + 재빌드
- Create: `docs/audit/2026-06-23-admin-payment-receipt-link-worklog.md`

- [ ] **Step 1: 목록 렌더 e2e 추가** — receipt 보유/미보유 결제가 목록에 각각 링크/`-`로 보이는지

기존 결제목록 e2e의 셋업(어드민 로그인 + 결제 2건 생성: 하나는 `raw_response={"receipt":{"url":"https://x/r"}}`, 하나는 `raw_response=None`)을 활용해:
```python
# GET /admin/payments 응답 HTML 검증
assert "매출전표" in html                     # 헤더 열 존재
assert 'href="https://x/r"' in html           # receipt 보유 결제의 링크
# 미보유 결제 행에는 링크 대신 '-' (해당 행 범위 내 href 없음) — 셋업에 맞춰 검증
```
> 결제 생성·어드민 인증 픽스처는 해당 e2e 파일의 기존 패턴을 그대로 사용. raw_response를 지정해 결제를 만드는 팩토리/직접 insert 방식 확인 후 사용.

- [ ] **Step 2: e2e 통과 확인**

Run: `uv run pytest tests/e2e/ -k "payment" -q`
Expected: PASS

- [ ] **Step 3: 매뉴얼 갱신** — 어드민 콘솔 결제 목록 문서

대상 식별: `grep -rln "결제 목록\|payments\|결제 내역" docs/manual/dev_manual docs/user_manual`. 결제 목록을 설명하는 .md에 "각 행의 '매출전표' 열에서 토스 매출전표(영수증)를 새 탭으로 연다(카드결제 DONE 건). 테스트 환경은 링크만 생성되고 실제 발행은 안 됨." 추가.

- [ ] **Step 4: 매뉴얼 재빌드**

Run: `uv run --with markdown python docs/user_manual/build.py` (+ dev_manual 빌드 스크립트 있으면 실행)
Expected: 재생성 완료.

- [ ] **Step 5: 워크로그 작성** — `docs/audit/2026-06-23-admin-payment-receipt-link-worklog.md`

내용: 목적(어드민 목록 매출전표 링크), 결정(저장된 raw_response.receipt.url 직접 링크·목록만·없으면 `-`), 변경 파일(app/admin/__init__.py, payments/list.html, 테스트, 매뉴얼), 검증(단위+e2e 통과), 토스 테스트환경 주의, 설계·계획 문서 링크.

- [ ] **Step 6: 커밋**

```bash
git add tests/ docs/
git commit -m "docs/test: 매출전표 링크 e2e + 매뉴얼 + 워크로그"
```

---

## Self-Review (작성자 점검)

- **Spec coverage:** 헬퍼+등록(T1)·목록 열/링크/colspan(T1)·표시조건 graceful(헬퍼 None)·테스트(T1 단위 + T2 e2e)·매뉴얼·워크로그(T2). 스펙 전 항목 커버. 범위 목록만 준수, 상세 제외.
- **Placeholder scan:** 헬퍼·템플릿·단위테스트는 완전 코드. e2e는 "기존 결제목록 픽스처 사용 + raw_response 지정 결제 생성" 패턴 명시(파일별 픽스처가 달라 고정 코드 대신 패턴 지시) — 단위 테스트가 핵심 로직을 이미 커버하므로 충분.
- **Type consistency:** `receipt_url(payment) -> str | None` T1 정의 → 템플릿/e2e에서 동일 사용.
