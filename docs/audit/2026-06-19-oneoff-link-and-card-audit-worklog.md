# 서비스 상세 일반결제 → 결제상세 이동 + 카드 이벤트 감사로그 상세화 워크로그

- 날짜: 2026-06-19
- 작업자: seungjinhan

## 요청

1. 서비스 상세 "일반결제"에서 주문번호(행)를 누르면 결제 상세를 보여줄 것.
2. 카드 등록·활성화·비활성화 등 **모든 카드 이벤트**가 감사로그에 상세한 정보로 남을 것.

## 변경 내용

### 1. 일반결제 행 → 결제 상세 (`services/_oneoff_table.html`)
- 행에 `onclick="location.href='/admin/payments/{p.id}'"` + 주문번호 셀 링크색 강조.

### 2. 카드 이벤트 감사로그 상세화
- `app/admin/audit_labels.py`:
  - `ACTION_LABELS`에 `card.register/replace/delete/activate/deactivate` 한글 라벨 추가.
  - `TARGET_TYPE_LABELS["card"]="카드"`, `_DETAIL_FIELDS`에 `card_number`/`issuer` 추가.
  - `service_id`는 원시 UUID라 화면 표시에서 제외(스코프 필터 전용) — `service_name`만 표시. (계정 이벤트의 중복 UUID 표시도 함께 정리됨)
- `app/admin/routes/audit.py`: `_resolve_names`에 `Card`(external_user_id) 추가, `_TARGET_TABLE["card"]="cards"` → 감사 목록 '대상' 컬럼이 "카드 · {사용자}"로 표시.
- `app/services/cards.py`: `_card_audit_detail(card, **extra)` 공통 빌더 추가 — 모든 카드 감사(등록/교체/삭제/활성/비활성)가 `external_user_id·service_id·card_number(마스킹)·issuer`를 남기도록 통일.
- `app/admin/routes/services.py` `_events_tab`: `target_type='card'` AND `detail.service_id==service_id` 이벤트를 서비스 상세 "이벤트" 섹션에 포함.

### 3. 테스트
- `tests/unit/test_audit_labels_card.py`(3) — 카드 액션 한글 라벨, 대상 라벨, detail 요약(service_id 원시값 미표시).
- `tests/e2e/test_card_admin.py` — `test_service_detail_events_show_card_events`(카드 등록/비활성화가 이벤트 섹션에 한글로 표시).
- `tests/e2e/test_service_detail_page.py` — 일반결제 행에 `/admin/payments/{id}` 링크 검증 추가.

### 4. 문서
- `admin/03-services.md`(일반결제 행 클릭·이벤트 섹션 카드 포함), `admin/09-audit.md`(카드 action 표·_TARGET_TABLE·필터), `16-card-vault.md`(공통 detail) 갱신 + HTML 재빌드.

## 검증

- `uv run pytest` → **581 passed**(신규 4건 포함).
