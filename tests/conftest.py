import base64
import os
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.models import Base

# DB는 별도 docker로 따로 구성한다(개발·배포 공통). 테스트도 외부 DB의 payment_test를 쓴다.
# 기본값은 외부 payment-postgres(host 5432, user payment). 다른 DB면 TEST_DATABASE_URL로 덮어쓴다.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://payment:Payment!2002@localhost:5432/payment_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")
TEST_ENCRYPTION_KEY = base64.b64encode(b"\x01" * 32).decode()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        encryption_key=TEST_ENCRYPTION_KEY,
        toss_secret_key="test_sk_dummy",
        scheduler_enabled=False,
        webhook_ip_check_enabled=True,
        toss_webhook_allowed_ips=["127.0.0.1"],  # httpx ASGITransport 클라이언트 IP
        _env_file=None,  # .env 무시 — 테스트 격리
    )


@pytest.fixture(scope="session")
def cipher(settings) -> AesGcmCipher:
    return AesGcmCipher(settings.encryption_key)


@pytest.fixture(scope="session")
async def engine(settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def redis_client(settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def clean_db(engine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {names} CASCADE"))


@pytest.fixture
async def clean_redis(settings) -> AsyncIterator[None]:
    yield
    client = Redis.from_url(settings.redis_url)
    await client.flushdb()
    await client.aclose()


from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.notifications.email import RecordingEmailSender
from app.toss.fake import FakeTossClient


@pytest.fixture
def fake_toss() -> FakeTossClient:
    return FakeTossClient()


@pytest.fixture
def email_sender() -> RecordingEmailSender:
    return RecordingEmailSender()


@pytest.fixture
def notifier() -> "RecordingServiceNotifier":
    """서비스 알림 발송기(테스트용) — 보낸 알림을 sent에 기록한다."""
    from app.notifications.service_notify import RecordingServiceNotifier
    return RecordingServiceNotifier()


@pytest.fixture
async def app(settings, engine, fake_toss, email_sender, notifier):
    application = create_app(settings, toss_client=fake_toss,
                             email_sender=email_sender, notifier=notifier, engine=engine)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# 테스트 결과 리포트 자동 생성
#   `uv run pytest` 실행이 끝나면 docs/test_report/ 에 HTML·Markdown 리포트를 남긴다.
#   외부 플러그인 없이 pytest 내장 훅만 사용한다(요청: 실행결과 레포팅 문서).
# ─────────────────────────────────────────────────────────────────────────────
import html as _html                                          # noqa: E402
import time as _time                                          # noqa: E402
from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402
from pathlib import Path as _Path                             # noqa: E402

_KST = _tz(_td(hours=9))                                      # 표시용 한국 시각
_REPORT_DIR = _Path(__file__).resolve().parent.parent / "docs" / "test_report"


class _ReportCollector:
    """세션 동안 테스트별 결과(통과/실패/스킵/에러·소요시간·실패 메시지)를 모아 종료 시 기록."""

    def __init__(self) -> None:
        self._start = _time.time()
        self._results: dict[str, dict] = {}   # nodeid -> {outcome, duration, message}

    def pytest_runtest_logreport(self, report) -> None:
        # call 단계 = 통과/실패/스킵, setup 실패 = error, setup 스킵 = skipped
        if report.when == "call":
            self._results[report.nodeid] = {
                "outcome": report.outcome,
                "duration": report.duration,
                "message": report.longreprtext if report.failed else "",
            }
        elif report.when == "setup" and report.outcome != "passed":
            self._results[report.nodeid] = {
                "outcome": "error" if report.failed else "skipped",
                "duration": report.duration,
                "message": report.longreprtext if report.failed else "",
            }

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        _write_test_report(self._results, _time.time() - self._start, int(exitstatus))


def pytest_configure(config) -> None:
    """리포트 수집기를 등록. xdist 워커 프로세스에서는 중복 방지를 위해 등록하지 않는다."""
    if hasattr(config, "workerinput"):
        return
    config.pluginmanager.register(_ReportCollector(), "devmanual-test-report")


def _write_test_report(results: dict, total_dur: float, exitstatus: int) -> None:
    """수집한 결과를 docs/test_report/report.md 와 report.html 로 기록한다."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for r in results.values():
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    total = sum(counts.values())
    failures = [(nid, r) for nid, r in results.items() if r["outcome"] in ("failed", "error")]
    slowest = sorted(results.items(), key=lambda kv: kv[1]["duration"], reverse=True)[:10]
    now = _dt.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M:%S KST")
    rate = (counts["passed"] / total * 100) if total else 0.0
    ok = exitstatus == 0

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Markdown ──
    md = [f"# 테스트 실행 리포트\n", f"- 실행 시각: {ts}",
          f"- 결과: {'✅ 성공' if ok else '❌ 실패'} (exit status {exitstatus})",
          f"- 합계: {total}건 · 통과 {counts['passed']} · 실패 {counts['failed']} · "
          f"에러 {counts['error']} · 스킵 {counts['skipped']}",
          f"- 통과율: {rate:.1f}%  · 소요시간: {total_dur:.1f}s\n"]
    if failures:
        md.append(f"## 실패/에러 ({len(failures)})\n")
        for nid, r in failures:
            msg = (r["message"] or "").strip()
            tail = msg[-1500:] if len(msg) > 1500 else msg
            md.append(f"### {r['outcome'].upper()} · `{nid}`\n\n```\n{tail}\n```\n")
    else:
        md.append("## 실패/에러\n\n없음 — 전부 통과했습니다.\n")
    md.append("## 가장 느린 테스트 (상위 10)\n")
    for nid, r in slowest:
        md.append(f"- {r['duration']:.2f}s · `{nid}`")
    (_REPORT_DIR / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # ── HTML (자체 포함·스타일) ──
    def esc(s: str) -> str:
        return _html.escape(s or "")

    fail_rows = ""
    for nid, r in failures:
        msg = (r["message"] or "").strip()
        tail = msg[-4000:] if len(msg) > 4000 else msg
        fail_rows += (f'<details><summary><span class="tag {r["outcome"]}">{r["outcome"].upper()}</span> '
                      f'<code>{esc(nid)}</code></summary><pre>{esc(tail)}</pre></details>\n')
    if not failures:
        fail_rows = '<p class="ok">없음 — 전부 통과했습니다. 🎉</p>'
    slow_rows = "".join(
        f"<tr><td>{r['duration']:.2f}s</td><td><code>{esc(nid)}</code></td></tr>"
        for nid, r in slowest)

    status_class = "ok" if ok else "bad"
    status_text = "✅ 성공" if ok else "❌ 실패"
    htm = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>테스트 실행 리포트 — {ts}</title>
<style>
 :root{{--primary:#476CFF;--g200:#F3F3F3;--g300:#E3E3E3;--g600:#9F9F9F;--g800:#3E3E3E;
   --green:#1FA463;--red:#FF4E51;--font:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
 body{{font-family:var(--font);color:#1a1a1a;max-width:980px;margin:0 auto;padding:28px;line-height:1.6}}
 h1{{font-size:24px;margin:0 0 4px}} .sub{{color:var(--g600);font-size:13px;margin-bottom:20px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 24px}}
 .card{{flex:1;min-width:120px;border:1px solid var(--g300);border-radius:12px;padding:14px 16px}}
 .card .n{{font-size:26px;font-weight:700}} .card .l{{font-size:12px;color:var(--g600)}}
 .status{{display:inline-block;padding:6px 14px;border-radius:8px;font-weight:700;font-size:15px}}
 .status.ok{{background:#E7F7EF;color:var(--green)}} .status.bad{{background:#FFEFEF;color:var(--red)}}
 .bar{{height:10px;border-radius:6px;background:var(--g200);overflow:hidden;margin:6px 0 18px}}
 .bar>i{{display:block;height:100%;background:var(--green)}}
 .n.pass{{color:var(--green)}} .n.fail{{color:var(--red)}}
 h2{{font-size:17px;margin:26px 0 10px;border-bottom:2px solid var(--primary);padding-bottom:6px}}
 details{{border:1px solid var(--g300);border-radius:8px;padding:8px 12px;margin:8px 0}}
 summary{{cursor:pointer}} code{{font-family:ui-monospace,Menlo,monospace;font-size:13px}}
 pre{{background:#1e2233;color:#e6e9f0;padding:12px;border-radius:8px;overflow-x:auto;font-size:12.5px}}
 .tag{{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:700;margin-right:6px}}
 .tag.failed,.tag.error{{background:#FFEFEF;color:var(--red)}}
 table{{border-collapse:collapse;width:100%;font-size:13.5px}}
 td{{border:1px solid var(--g300);padding:6px 10px}} td:first-child{{white-space:nowrap;color:var(--g800)}}
 .ok{{color:var(--green)}}
</style></head><body>
 <h1>테스트 실행 리포트</h1>
 <div class="sub">{ts} · <span class="status {status_class}">{status_text}</span> (exit status {exitstatus})
   · 소요시간 {total_dur:.1f}s</div>
 <div class="cards">
   <div class="card"><div class="n">{total}</div><div class="l">전체</div></div>
   <div class="card"><div class="n pass">{counts['passed']}</div><div class="l">통과</div></div>
   <div class="card"><div class="n fail">{counts['failed']}</div><div class="l">실패</div></div>
   <div class="card"><div class="n fail">{counts['error']}</div><div class="l">에러</div></div>
   <div class="card"><div class="n">{counts['skipped']}</div><div class="l">스킵</div></div>
   <div class="card"><div class="n">{rate:.1f}%</div><div class="l">통과율</div></div>
 </div>
 <div class="bar"><i style="width:{rate:.1f}%"></i></div>
 <h2>실패 / 에러 ({len(failures)})</h2>
 {fail_rows}
 <h2>가장 느린 테스트 (상위 10)</h2>
 <table>{slow_rows}</table>
 <p class="sub" style="margin-top:24px">생성: tests/conftest.py 의 리포트 훅 · 매 <code>uv run pytest</code> 실행 시 갱신</p>
</body></html>"""
    (_REPORT_DIR / "report.html").write_text(htm, encoding="utf-8")
