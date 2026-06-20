# 01. 첫날 — 셋업하고 직접 띄워보기

> 목표: 퇴근 전까지 ① 서버 기동 ② 어드민 로그인 ③ 샘플 쇼핑몰에서 실제 구독 1건 생성
> ④ 전체 테스트 통과 — 네 가지를 내 PC에서 확인한다.

## 1. 사전 준비

- **Docker Desktop** (PostgreSQL/Redis 컨테이너용)
- **uv** (파이썬 패키지/실행 관리자) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- 토스페이먼츠 **테스트 키**(`test_sk_…`, `test_ck_…`) — 팀 공유 계정의 개발자센터에서 확인

## 2. 인프라 기동 + 환경 설정

```bash
docker compose up -d        # PostgreSQL(127.0.0.1:5433) + Redis(127.0.0.1:6380)
cp .env.example .env.dev    # 환경별 설정 — 아래 두 값은 반드시 채울 것
```

`.env.dev`에서 채울 핵심 값:

```dotenv
# AES-256 암호화 키(빌링키·HMAC 시크릿 보관용) — 아래 명령으로 생성
# python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
ENCRYPTION_KEY=<생성한 base64 32바이트>
TOSS_SECRET_KEY=test_sk_...
```

> 환경 분리: 공통 `.env` → 환경별 `.env.dev`/`.env.prod`가 덮어쓴다.
> 실행 환경은 OS 환경변수 `APP_ENV`(기본 dev)로 정해진다.

## 3. DB 마이그레이션 + 관리자 계정

```bash
uv run alembic upgrade head                 # 전체 테이블 생성
uv run python -m app.cli create-admin --email admin@medisolveai.com --password '<10자 이상>'
```

## 4. 서버 기동 + 어드민 접속

```bash
uv run uvicorn app.main:app --reload --port 8000
```

- 어드민 콘솔: http://127.0.0.1:8000/admin — 방금 만든 계정으로 로그인
- 헬스체크: http://127.0.0.1:8000/health
- 데모 데이터가 필요하면: `uv run python scripts/seed_demo.py` (DEMO-* 서비스 생성 — 운영 DB 금지)

## 5. 샘플 쇼핑몰로 실제 구독 만들어보기

어드민에서 **서비스 등록**(허용 IP `127.0.0.1`) → 키 화면에서 API 키/HMAC Secret 복사 →
요금제 1개 생성. 그 다음:

```bash
cd sample_service
cp .env.example .env && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 8001
```

http://127.0.0.1:8001 접속 → 서비스 선택(복사한 키 입력) → 이메일 로그인 → 요금제 →
카드 등록(토스 테스트 모드 — 실제 청구 없음) → 어드민의 구독/결제 메뉴에서 방금 만든
구독을 확인한다. **샘플 화면마다 하단 「개발자 노트」가 그 화면의 API·코드를 설명한다 —
첫 주 학습 자료로 가장 좋다.**

## 6. 테스트 돌려보기

```bash
uv run pytest          # 전체 (docker compose가 떠 있어야 함 — 테스트 전용 DB/Redis 사용)
uv run pytest -q tests/unit            # 빠른 단위 테스트만
```

> ⚠️ **전체 테스트는 동시에 두 개 띄우지 마라.** 두 실행이 같은 테스트 DB(payment_test)를
> 쓰며 시작 시 스키마를 새로 만들기 때문에 서로를 파괴해 수십 건의 가짜 실패가 난다. (09장)

## 첫날 체크리스트

- [ ] `docker compose up -d` 후 `uv run pytest` 전체 통과
- [ ] 어드민 로그인 + 대시보드 확인
- [ ] 샘플에서 구독 1건 생성 → 어드민에서 확인
- [ ] `/my`에서 구독 취소 → 재개까지 눌러보기 (상태가 어떻게 변하는지 관찰)
