# 2026-06-18 워크로그 — 로그인 페이지에 매뉴얼 링크 추가

## 요청
로그인 페이지에 매뉴얼 링크를 붙여 달라.

## 결정(사용자 확인)
- 대상 매뉴얼: **서비스 담당자 매뉴얼**(`docs/manual/`, 다중 페이지 정적 사이트).
- 노출 범위: **공개(비로그인 허용)** — 기존 `/admin/intro`(서비스 가이드)와 동일 패턴.

## 변경 내용
1. **`app/main.py`** — `/manual` 에 `docs/manual` 정적 마운트 추가.
   - `StaticFiles(directory=docs/manual, html=True, check_dir=False)`.
   - `html=True`: `/manual/` → `index.html` 자동 반환(상대 링크·자산 동작).
   - `check_dir=False`: 운영 이미지에 디렉터리가 없어도 기동이 깨지지 않음(없으면 요청 시 404).
   - `/admin` 바깥(공개)에 마운트 → 로그인 전에도 열람 가능.
2. **`.dockerignore`** — `docs` 전체 제외는 유지하되 `!docs/manual` · `!docs/manual/**`로
   재포함 → 운영 이미지에 서비스 담당자 매뉴얼만 포함(나머지 docs는 계속 제외).
3. **`app/admin/templates/login.html`** — 기존 "📖 서비스 가이드" 링크 아래에
   "📚 전체 매뉴얼 보기 (설치·설정·배포·API)" 링크 추가(`/manual/`, 새 탭).
4. **`docs/manual/00-setup.html`** (docs-sync) — 1.9 로그인 단계에 매뉴얼이 `/manual/`로
   서빙되고 로그인 화면에서 링크된다는 note 추가.

## 검증
- `from app.main import app` → import 성공, `/manual` 마운트 등록 확인.
- TestClient: `/manual/`(200 html) · `/manual/00-setup.html`(200) · `/manual/assets/manual.css`(200 css).
- Docker 빌드 컨텍스트 테스트(`COPY docs ./docs`): `docs/` 하위에 **`docs/manual`만** 포함되고
  `index.html`·`assets/manual.css` 존재 확인 → 운영 이미지에서도 `/manual/` 동작.

## 참고/주의
- 매뉴얼이 공개 서빙되므로 인터넷 노출 서버에서는 설치·환경변수명 등 운영 정보가 비인증
  열람된다(비밀값은 없음). 기존 `/admin/intro` 공개 가이드와 동일한 수준의 노출.
- 비공개로 돌리려면 `/manual` 마운트를 `/admin` 하위 인증 라우트로 옮기면 됨.

## 후속 버그픽스 (배포 후 /manual 500 INTERNAL_ERROR)
배포 이미지에서 `/manual/` 접속 시 500 발생 — 컨테이너 안에 `/app/docs/manual`이 없었음.

원인(2가지):
1. **`.dockerignore` 인라인 주석** — `!docker/entrypoint.sh   # ...`, `!.env.example   # ...`
   처럼 패턴 줄 끝에 주석을 달았는데, `.dockerignore`는 **줄 끝 주석을 지원하지 않아**
   `# ...`까지 패턴의 일부로 해석 → 재포함이 깨져 `COPY docker/entrypoint.sh` 빌드 실패.
   → 주석을 모두 독립 줄로 분리.
2. **Dockerfile 누락 COPY** — `.dockerignore`는 "컨텍스트에서 제외할 것"만 정한다.
   실제로 이미지에 넣으려면 Dockerfile이 `COPY` 해야 하는데 `docs/`를 복사하지 않았음.
   → `COPY docs/manual ./docs/manual` 추가(app/main.py의 `/app/docs/manual` 마운트 경로와 일치).

재검증(빌드된 prod 이미지 내부):
- `ls /app/docs/manual/{index.html,00-setup.html,assets/manual.css}` → 존재.
- 이미지 내 TestClient: `/manual/`·`/manual/00-setup.html`·`/manual/assets/manual.css` 모두 200.

배포 절차: 서버에서 `docker compose -f docker-compose.prod.yml up -d --build` 로 재빌드·재기동해야 반영됨(기존 이미지 캐시가 아니라 새 이미지 필요).
