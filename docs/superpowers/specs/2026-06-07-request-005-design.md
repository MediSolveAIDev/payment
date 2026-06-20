# 요청 005 — 요금제 금액 정책 + 서비스 상세 UX + htmx 부분 갱신 설계

날짜: 2026-06-07
상태: 승인됨
요청: docs/requests/005.md

## 목표

1. 첫결제 금액을 상시할인과 무관하게 **정가 기준**으로 변경 (표시 + 실제 결제 청구 모두)
2. 요금제 리스트에 상시할인 컬럼 추가, 정기결제액 셀의 할인 비율 배지 제거
3. 첫결제액/정기결제액 hover 시 계산 내역 툴팁
4. 허용 IP를 옥텟 4칸 입력(숫자만, IPv4 전용) + 행 추가/삭제
5. 비활성화/키재발급/삭제 버튼을 상단 서비스 이름 옆으로
6. 키 복사 모달 (API 키 암호화 저장 추가)
7. 관리자 할당을 개요 카드로 통합 (추가 버튼 + 이메일 옆 수정/삭제)
8. 요금제 비활성화/삭제 시 부분 갱신 (htmx)
9. 모든 admin 리스트의 정렬/필터/페이징 부분 갱신 (htmx)

## 1. 첫결제 금액 정책 (요청 1.1) — 실제 결제 변경

- `app/services/billing_math.py`의 `plan_first_amount(plan)`:
  - 변경 전: `compute_first_amount(plan_recurring_amount(plan), ...)` (상시 할인가에 중첩)
  - 변경 후: `compute_first_amount(plan.price, ...)` (**정가에 첫구독 할인만**)
- 이 함수는 실제 첫 결제 청구(`app/services/subscriptions.py`)와 admin 표시 양쪽에서
  사용되므로 한 곳 변경으로 일관 적용된다.
- `plans/form.html`의 JS 미러 동일 변경: 첫 결제 = `applyDiscount(price, first_payment_type, v)`
  (정기 금액과 무관). FREE → 0 유지.
- 폼의 "첫 결제는 위 첫구독 할인과 중첩 적용됩니다" 안내 문구를
  "첫 결제는 정가 기준으로 첫구독 할인만 적용됩니다"로 수정.
- 기존 테스트 기대값 갱신: 단위(`plan_first_amount` 중첩 테스트), e2e
  (정가 10,000/상시 5%/첫구독 −1,000 → 첫결제 9,500→**9,000**, 정기 9,500 유지).

## 2. 요금제 리스트 — 상시할인 컬럼 + 툴팁 (요청 1.2, 1.3)

- 대상: `/admin/plans`(`plans/list.html`), 서비스 상세 요금제 테이블(`services/detail.html`)
- 컬럼 구성: 정가 | 첫 결제액 | 정기 결제액 | **상시할인**(신규) | ...
  - 상시할인 표기: `DISCOUNT_PERCENT` → "5%", `DISCOUNT_AMOUNT` → "1,000원", NONE → "−"
- 정기 결제액 셀의 할인 배지(`badge-TRIAL` 비율 표시) 제거 — 금액만 표시.
- 툴팁: 첫결제액/정기결제액 `<td>`에 native `title` 속성으로 계산 내역.
  - 첫결제액 예: "정가 10,000원 − 첫구독 할인 1,000원 = 9,000원" (할인 없으면 "정가 10,000원")
  - 정기결제액 예: "정가 10,000원 − 상시 할인 5% = 9,500원" (할인 없으면 "정가 10,000원")
  - 계산 문자열은 서버에서 생성: `billing_math`에 `first_amount_breakdown(plan) -> str`,
    `recurring_amount_breakdown(plan) -> str` 헬퍼 추가, 라우트에서 표시용 속성으로 부여.

## 3. 허용 IP 옥텟 4칸 입력 (요청 2.1)

- 적용: 서비스 상세(`services/detail.html`) + 서비스 등록(`services/new.html`)
- UI: 한 행 = 4칸(`inputmode="numeric"`, maxlength 3, 0~255만, 3자리 입력 시 자동
  다음 칸 포커스) + 행 삭제 버튼. 목록 아래 "IP 추가" 버튼으로 행 추가.
  기존 IP는 행으로 미리 채움(상세). 등록 폼은 빈 행 1개로 시작.
- 제출: JS가 submit 시 각 행을 `a.b.c.d`로 합성해 hidden `allowed_ips` 필드에
  줄 단위로 채움 → 서버 `_parse_ips`는 변경 없음.
- 빈 행(4칸 모두 공백)은 무시. 일부만 채워진 행은 submit 차단 + 행에 시각적 표시.
- 서버 검증 IPv4 전용: `registry._validate_ips`가 `ipaddress.IPv4Address`만 허용
  ("유효하지 않은 IP" 메시지 유지). 기존 IPv6 데이터는 없다고 가정(사내 IPv4 환경).
- 구현 JS는 `app/static/admin.js`에 추가 (data-속성 기반 — `data-ip-rows` 컨테이너).

## 4. 상단 액션 버튼 (요청 2.2)

- `services/detail.html`의 page-head(서비스명 + 상태 배지 옆, 우측 정렬)에
  비활성화/활성화·키 재발급·삭제 버튼 3개 이동 (`data-confirm` 모달 속성 유지).
- 개요 카드의 actions 블록 제거.

## 5. 키 복사 모달 (요청 2.3) — DB 마이그레이션 포함

- 모델: `Service.api_key_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)`
  추가. 인증용 `api_key_hash`는 그대로 유지(인증 경로 무변경).
- Alembic 마이그레이션 1건 (nullable 컬럼 추가 — 무중단).
- `registry.register_service`/`rotate_keys`: 평문 API 키를 `cipher.encrypt`로 저장.
- 상세 페이지에 "키 복사" 버튼(상단 액션 옆) → htmx `hx-get`으로
  `GET /admin/services/{id}/keys-modal` fragment 로드 → 모달 표시:
  - API 키(복호화) + HMAC secret(복호화) 각각 마스킹 표시 + 복사 버튼(`data-copy` 재사용)
  - `api_key_encrypted`가 없는 기존 서비스: "키 재발급 후 복사할 수 있습니다" 안내만 표시
  - 조회 시 감사 로그 `service.keys_viewed` 기록 (audit_labels에 한글 라벨 추가)
- 모달 마크업은 fragment 템플릿(`services/_keys_modal.html`), 닫기 버튼 포함.

## 6. 관리자 할당 → 개요 통합 (요청 2.4)

- 관리자 할당 카드 제거. 개요 카드에 담당자 섹션:
  - 담당자 행: 이메일(계정 상세 링크) + 상태 배지 + **수정**(`/admin/users/{id}/edit` 링크)
    + **삭제**(담당 해제 폼, `data-confirm` 유지)
  - "담당자 추가" 버튼: 클릭 시 인라인 폼(셀렉트 + 추가 버튼) 토글 표시
    (할당 가능 계정 없으면 기존처럼 계정 추가 링크 안내)
- 라우트(`assign-manager`, `managers/{id}/remove`)는 변경 없음.

## 7. htmx 부분 갱신 (요청 2.5, 2.6)

### 공통 인프라
- 각 리스트의 "테이블 + 페이저(+빈 상태)" 영역을 partial 템플릿로 분리:
  - `services/_table.html`, `plans/_table.html`, `subscriptions/_table.html`,
    `users/_table.html`, `audit/_table.html`, `services/_subs_table.html`(상세 내 구독)
  - 본 페이지 템플릿은 `{% include %}`로 partial 포함. partial 바깥에 고정
    `id`(예: `id="list-services"`) 래퍼.
- `_list.html` 매크로 확장: `toolbar`/`sort_th`/`pager`에 hx 속성 추가 —
  `hx-get`(동일 URL), `hx-target`(`#list-<name>`), `hx-swap="outerHTML"`,
  `hx-push-url="true"`(URL 동기화 — 새로고침/북마크 호환). 매크로에 target id 파라미터 추가.
- 라우트: `request.headers.get("HX-Request")`이면 partial 템플릿만 렌더, 아니면 전체
  페이지. 공통 헬퍼 `render_list(request, full_tpl, partial_tpl, **ctx)`를
  `app/admin/__init__.py`에 추가.
- 적용 리스트: 서비스, 요금제, 구독, 계정, 감사 + 서비스 상세 구독 리스트.
  (서비스 상세 구독 리스트는 페이지에 다른 영역이 많으므로 partial 필수.)

### 요금제 비활성화/삭제 부분 갱신 (서비스 상세)
- 요금제 테이블 영역을 partial(`services/_plans_table.html`)로 분리, 래퍼
  `id="list-svc-plans"`.
- 비활성화/삭제 폼을 `hx-post` + `hx-target="#list-svc-plans"` + `hx-swap="outerHTML"`로
  전환. 서버는 처리 후 HX-Request면 갱신된 요금제 테이블 partial 렌더
  (실패 시 partial 상단에 error 블록).
- `admin.js`의 `data-confirm` 모달이 htmx 요청도 가로채도록 확장:
  htmx `configRequest`/`confirm` 이벤트 연동 (모달 확인 시에만 요청 발사).
- lucide 아이콘 재렌더는 기존 `htmx:afterSwap` 핸들러가 이미 처리.

## 에러 처리

- htmx partial 응답에도 error 표시: partial 템플릿 안에 `{% if error %}` 블록 포함.
- IPv4 검증 실패는 기존 `?error=` 경로(전체 페이지) 유지 — IP 폼은 일반 POST 그대로.
- 키 모달은 관리자 권한(`require_admin`) + CSRF 불필요(GET 조회) — 단 감사 로그 필수.

## 테스트

- 단위: `plan_first_amount` 정가 기준(상시할인 무시), breakdown 문자열 헬퍼,
  IPv4 전용 `_validate_ips`(IPv6 거부)
- 통합: 신규 구독 첫 결제 청구액이 정가 기준으로 계산되는지(기존 테스트 기대값 갱신)
- e2e:
  - 리스트 상시할인 컬럼/배지 제거/title 툴팁 존재
  - 키 모달 fragment: 암호화 저장된 서비스는 키 노출+감사 로그 기록, 미저장 서비스는 안내 문구
  - page-head에 액션 버튼, 개요에 담당자 수정/삭제/추가 UI
  - HX-Request 헤더로 정렬 요청 시 partial만 응답(`<html` 미포함, 테이블 포함),
    헤더 없으면 전체 페이지
  - 요금제 비활성화 hx-post 후 partial 응답에 갱신된 상태 반영
  - IP 옥텟 UI: DOM 구조(4칸 행, hidden 필드, IP 추가 버튼) 존재 + hidden 값 제출 동작
- JS 동작(옥텟 포커스 이동, 행 합성)은 DOM 구조 검증까지만(로직은 단순, 서버 검증이 최종 방어)

## 변경하지 않는 것

- API 키 인증 경로(`api_key_hash` 비교) 무변경 — 암호화 컬럼은 모달 표시 전용
- 토스페이먼츠 연동, 스케줄러, 외부 API 스키마 무변경
- 데이터 모델은 `Service.api_key_encrypted` 추가 외 무변경
- 검색 폼 입력 중 자동 검색(키 입력 디바운스) 같은 추가 UX는 범위 외 (정렬/필터/페이징의
  부분 갱신만)

## 작업 순서 (계획 수립 시 기준)

1. 금액 정책 변경(1.1) — 결제 로직이므로 최우선 격리
2. 리스트 컬럼+툴팁(1.2, 1.3)
3. API 키 암호화 저장 + 모달(2.3) — 마이그레이션 포함
4. 상단 버튼 + 관리자 할당 통합(2.2, 2.4) — 레이아웃
5. IP 옥텟 입력(2.1)
6. htmx 리스트 partial 인프라 + 전 리스트 적용(2.6)
7. 요금제 액션 부분 갱신(2.5)
