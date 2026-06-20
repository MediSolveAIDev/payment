# 서비스 상세 — 등록 카드 리스트 추가 워크로그

- 날짜: 2026-06-19
- 작업자: seungjinhan
- 요청: 어드민 **서비스 상세**에 이 서비스에 등록된 카드 정보가 리스트로 나와야 한다. 카드정보·사용자정보 등 카드 관련 정보를 모두 표시.

## 배경

직전 작업(`08b4745`)에서 **구독 상세**에는 `cards` 테이블 조회로 카드 1건을 표시했다.
이번에는 같은 패턴을 **서비스 상세**로 확장해, 해당 서비스에 결제수단을 등록한
사용자별 카드 목록(=결제수단 보관함 내역)을 한 화면에서 보도록 했다.

## 변경 내용

### 1. 라우트 (`app/admin/routes/services.py`)
- `app.models`에서 `Card` import 추가.
- `CARDS_SORT`(external_user_id/created_at/updated_at), `CARDS_PER_PAGE=10` 상수 추가.
- `_cards_tab()` 신규 — `select(Card).where(service_id==...)`를 `kpage` 파라미터로 페이징하고
  사용자 ID 부분검색(`q`, ilike)을 지원. `flatten=True`로 Card 평탄화.
- `services_detail()`에서 `_cards_tab()` 호출 결과(`card_page`, `kpp`)를 템플릿에 전달.
- htmx 분기 맵에 `"list-svc-cards": "services/_cards_table.html"` 추가.

### 2. 템플릿
- 신규 `app/admin/templates/services/_cards_table.html` — `list-svc-cards` 컨테이너.
  컬럼: 사용자 / 카드번호(마스킹) / 발급사코드 / customerKey / 빌링키 해시(앞 12자, 전체는 title 툴팁) / 등록일 / 변경일.
  검색 툴바 + 정렬 헤더(사용자·등록일·변경일) + 페이저(`_list.html` 매크로 재사용).
  **빌링키 암호문(`billing_key_encrypted`)은 표시하지 않음.**
- `services/detail.html` — 구독 테이블과 일반결제 테이블 사이에 `_cards_table.html` include 추가.

### 3. 테스트 (`tests/e2e/test_service_detail_page.py`)
- `test_detail_shows_registered_cards` — 카드 등록(FakeToss) 후 서비스 상세에 사용자 ID·마스킹 번호 표시, 타 서비스 카드는 미표시(스코프) 검증.
- `test_detail_cards_htmx_partial_and_paging` — 11건 등록 → `kpage=2` htmx 부분 응답에 partial만 오고 마지막 1건만 표시되는지 검증.

### 4. 매뉴얼 (dev_manual)
- `16-card-vault.md` — "관리자 화면 — 등록 카드 표시" 섹션 추가(구독 상세/서비스 상세 비교 + 컬럼·페이징·보안 설명).
- `admin/03-services.md` — 하단 탭을 3개→4개로, 등록 카드 탭 컬럼 설명 추가.
- `build_html.py`로 HTML 재빌드 + `docs/manual/dev_manual` 동기화.

## 검증

- `uv run pytest tests/e2e/test_service_detail_page.py -q` → **27 passed**.
- 라우트 모듈 import·Jinja 템플릿 파싱 정상 확인.

## 참고

- `card_info`는 토스 `issue_billing_key` 응답의 `card` 객체(JSONB) — 주로 `number`(마스킹), `issuerCode`. 미등록 키는 `-` 처리.
- 카드는 `(service_id, external_user_id)`당 1건이므로 목록은 사용자별 1행.
