# 사용자·개발자 매뉴얼 신규 작성(HTML) 워크로그 — 요청 018

- 날짜: 2026-06-20
- 작업자: seungjinhan
- 요청: `docs/requests/018_메뉴얼.md` — 사용자 매뉴얼 + 개발자 매뉴얼을 새 `user_manual` 폴더에 HTML로, 이해하기 쉽게 작성.

## 결과물

`docs/user_manual/` — 마크다운 소스 + 빌드된 HTML 사이트(좌측 네비·우측 목차·검색·콜아웃·프로세스 다이어그램). **`docs/user_manual/index.html`**에서 시작.

### 빌더
- **`docs/user_manual/build.py`** — 마크다운 → 스타일 HTML(검증된 `dev_manual` 빌더 적응). 2개 그룹(사용자/개발자), `.flow` 프로세스 다이어그램·`.steps`·`.pill` 컴포넌트 추가, CSS/JS는 `dev_manual` 자산 재사용.
- 실행: `uv run --with markdown python docs/user_manual/build.py` → index + 17페이지 + assets.

### 페이지(17개)
**사용자 매뉴얼** — 00 전체 개요(8단계 프로세스 그림), 01 관리자 콘솔, 02 카드 관리, 03 구독 관리, 04 요금제 관리, 05 일반결제·환불, 06 계정 관리, 07 전체 설정, 08 감사로그, 09 대시보드(구독·결제·정산·요금제).
**개발자 매뉴얼** — 10 설치·설정·배포(docker·외부 DB·3-service), 11 서비스 API 레퍼런스(HMAC·카드/구독/결제/알림), 12 카드 기능 코드 흐름, 13 구독 기능 코드 흐름, 14 일반결제·취소·정산 코드 흐름, 15 서비스 알림 코드 흐름, 16 어드민 화면별 설명.

## 작업 방식

- 검증된 `dev_manual` 빌더를 사용자 친화 테마로 적응시켜 일관된 HTML을 생성.
- 17개 페이지 콘텐츠는 **병렬 서브에이전트 10개**로 분담 작성(서로 다른 파일 → 충돌 없음). 각 에이전트가 실제 코드·기존 docs를 읽어 사실 기반으로 마크다운 작성.
- 작성 규칙(H1 번호·콜아웃 분류·`.flow`/`.steps`/`.pill`·코드위치 배지·내부 .md 링크)을 공통 지시로 통일.

## 검증

- 빌드 성공: index + 17페이지 + assets.
- 플레이스홀더(작성 예정) 0건, 미변환 `.md` 링크 0건(깨진 내부 링크 1건 `13-feature-card-vault.md`→`12-feature-card.md` 수정).
- 컴포넌트 렌더 확인: 진입 카드 2, 개요 프로세스 그림 8단계, 콜아웃(easy/note/ref), 개발자 페이지 코드블록·표 다수(예: 11=16 pre/14 table, 16=화면별 표).
- 모든 내용은 실제 코드·기존 매뉴얼(`docs/dev_manual`)·audit 워크로그에서 확인한 사실 기반.

## 후속 보강

- **전체 개요(00-overview)에 '서비스 입장 연동 시퀀스 그림' 추가** — 외부 서비스(앱) ↔ 결제 서버 ↔ 토스 3레인 SVG 시퀀스 다이어그램으로 ①카드 등록 → ②구독 요청 → ③자동연장(서버) → ④알림 수신 → ⑤필요 시 추가 호출을 그림으로 표현(서비스가 직접 호출하는 부분은 파란색). 빌더에 시퀀스 다이어그램용 CSS(`.seqwrap`) 추가.
- **링크 버그 수정** — `<ol class="steps">` 등 raw HTML 블록 안의 마크다운 링크 `[글](파일.md)`가 변환되지 않던 문제를, 빌더에 후처리(`convert_leftover_md_links`, `<pre>/<code>` 보호)로 일괄 변환. 전 17페이지의 steps/seq 내부 링크가 정상 `<a href=...html>`로 동작.
- **설치·배포(10)에 '새 리눅스 서버에 처음 설치하기(5.0)' 추가** — 빈 Ubuntu 서버 기준 전체 절차: Docker 설치 → 코드 가져오기 → 외부 PostgreSQL 준비(host.docker.internal/extra_hosts) → `.env.prod`·ENCRYPTION_KEY 생성 → TLS 인증서 → `docker compose -f docker-compose.prod.yml up -d --build`(엔트리포인트 자동 마이그레이션) → 최초 관리자 `python -m app.cli create-admin` → 방화벽(80/443) → 동작 확인 → 업데이트(git pull + up -d --build).

- **가로 전체 폭 사용** — 빌더 CSS(append override)에 `.main{max-width:none}`·`.main-index{max-width:none}` 추가해 본문을 화면 가로 전체로 확장(중앙 1180/1080 제한 해제). user_manual에만 적용, dev_manual은 영향 없음.

## 비고

- 기존 `docs/manual`·`docs/dev_manual`은 그대로 두고, 새 기능 반영 + 사용자/개발자 통합 안내를 위해 `docs/user_manual`로 신규 작성(요청대로).
- 콘텐츠 수정 시 해당 `.md`만 고치고 `build.py` 재실행하면 HTML이 갱신된다.
