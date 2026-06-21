#!/usr/bin/env python3
"""
어드민 콘솔 화면을 자동 캡처해 docs/user_manual/assets/img/ 에 PNG로 저장한다.

매뉴얼(assets/img/README.md)의 파일명 매핑대로 전체 페이지(full page) 스크린샷을 만든다.

준비:
    pip install playwright
    playwright install chromium

실행 (로그인 정보는 환경변수로 전달 — 코드에 비밀번호를 적지 않는다):
    BASE_URL=https://localhost \
    ADMIN_EMAIL=you@example.com \
    ADMIN_PASSWORD='****' \
    python docs/user_manual/capture_screens.py

옵션 환경변수:
    BASE_URL        기본 https://localhost
    SCALE           device scale factor (기본 2 = 레티나 고해상도)
    WIDTH           뷰포트 폭 (기본 1280)
    HEADED          1이면 브라우저 창을 띄워서 진행 과정 확인

동작 메모:
    - 자체서명 HTTPS 인증서를 무시한다(ignore_https_errors).
    - 페이지가 unpkg.com(htmx/lucide)을 동기 로드하는데 이 응답이 자주 멈춘다.
      그래서 unpkg 요청을 jsdelivr에서 받은 동일 라이브러리 내용으로 대체(fulfill)해
      로딩이 정상 완료되도록 한다. (실패 시 빈 스크립트로 대체하고 계속 진행)
    - 상세/카드 페이지는 목록 화면의 행 링크에서 첫 ID를 뽑아 이동한다.
    - 항목별로 try/except 처리 — 일부 실패해도 나머지는 계속 캡처한다.
"""

import os
import re
import sys
import ssl
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "https://localhost").rstrip("/")
EMAIL = os.environ.get("ADMIN_EMAIL")
PASSWORD = os.environ.get("ADMIN_PASSWORD")
SCALE = float(os.environ.get("SCALE", "2"))
WIDTH = int(os.environ.get("WIDTH", "1280"))
HEADED = os.environ.get("HEADED") == "1"

OUT_DIR = Path(__file__).resolve().parent / "assets" / "img"

# unpkg 대체용 라이브러리 (페이지가 요청하는 것과 동일 버전)
VENDOR = {
    "htmx": "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js",
    "lucide": "https://cdn.jsdelivr.net/npm/lucide@latest/dist/umd/lucide.min.js",
}

# 정적 경로: (파일명, URL경로)
STATIC_TARGETS = [
    ("dashboard.png", "/admin"),
    ("subscriptions-list.png", "/admin/subscriptions"),
    ("plans-list.png", "/admin/plans"),
    ("plan-form.png", "/admin/plans/new"),
    ("payments-list.png", "/admin/payments"),
    ("accounts-list.png", "/admin/users"),
    ("account-new.png", "/admin/users/new"),
    ("settings.png", "/admin/settings"),
    ("audit.png", "/admin/audit"),
    ("settlement.png", "/admin/settlement"),
    ("service-new.png", "/admin/services/new"),
]

RESERVED = {"new", "export.xlsx", "edit"}


def log(msg):
    print(msg, flush=True)


def fetch_vendor():
    """jsdelivr에서 htmx/lucide 본문을 받아둔다. 실패하면 빈 문자열."""
    ctx = ssl.create_default_context()
    out = {}
    for name, url in VENDOR.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "capture-script"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                out[name] = r.read().decode("utf-8", "replace")
            log(f"  vendor: {name} 다운로드 OK ({len(out[name])} bytes)")
        except Exception as e:
            out[name] = ""
            log(f"  vendor: {name} 다운로드 실패({e}) — 빈 스크립트로 대체")
    return out


def make_unpkg_handler(vendor):
    def handler(route):
        url = route.request.url
        body = ""
        if "lucide" in url:
            body = vendor.get("lucide", "")
        elif "htmx" in url:
            body = vendor.get("htmx", "")
        route.fulfill(
            status=200,
            content_type="application/javascript; charset=utf-8",
            body=body,
        )
    return handler


def settle(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(700)  # 아이콘/차트 렌더 여유


def shot(page, filename, url):
    settle(page, url)
    path = OUT_DIR / filename
    page.screenshot(path=str(path), full_page=True)
    log(f"  ✓ {filename}  ←  {url}")


def first_id(page, section):
    """현재 페이지 HTML에서 /admin/<section>/<id> 의 첫 ID를 추출."""
    html = page.content()
    ids = re.findall(rf"/admin/{re.escape(section)}/([^/'\"?\s]+)", html)
    for i in ids:
        if i not in RESERVED and "." not in i:
            return i
    return None


def main():
    if not EMAIL or not PASSWORD:
        log("ERROR: ADMIN_EMAIL / ADMIN_PASSWORD 환경변수를 설정하세요.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log(f"출력 폴더: {OUT_DIR}")
    log(f"대상 서버: {BASE_URL}")

    log("라이브러리 준비(jsdelivr) ...")
    vendor = fetch_vendor()

    ok, fail = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not HEADED)
        context = browser.new_context(
            viewport={"width": WIDTH, "height": 900},
            device_scale_factor=SCALE,
            ignore_https_errors=True,
        )
        context.route("**/unpkg.com/**", make_unpkg_handler(vendor))
        page = context.new_page()

        # 1) 로그인 화면 (아직 비로그인 상태)
        try:
            shot(page, "login.png", f"{BASE_URL}/admin/login")
            ok.append("login.png")
        except Exception as e:
            fail.append(("login.png", str(e)))

        # 2) 로그인
        try:
            page.goto(f"{BASE_URL}/admin/login", wait_until="domcontentloaded")
            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url("**/admin", timeout=15000)
            log("로그인 성공")
        except Exception as e:
            log(f"로그인 실패: {e}")
            # 로그인 화면 외에는 의미가 없으므로 종료
            browser.close()
            _summary(ok, fail)
            sys.exit(2)

        # 3) 정적 페이지들
        for filename, path in STATIC_TARGETS:
            try:
                shot(page, filename, f"{BASE_URL}{path}")
                ok.append(filename)
            except Exception as e:
                fail.append((filename, str(e)))
                log(f"  ✗ {filename}: {e}")

        # 4) 상세 페이지들 (목록에서 첫 ID 추출)
        # 구독 상세
        try:
            settle(page, f"{BASE_URL}/admin/subscriptions")
            sid = first_id(page, "subscriptions")
            if sid:
                shot(page, "subscription-detail.png", f"{BASE_URL}/admin/subscriptions/{sid}")
                ok.append("subscription-detail.png")
            else:
                fail.append(("subscription-detail.png", "목록에 구독 없음"))
        except Exception as e:
            fail.append(("subscription-detail.png", str(e)))

        # 결제 상세
        try:
            settle(page, f"{BASE_URL}/admin/payments")
            pid = first_id(page, "payments")
            if pid:
                shot(page, "payment-detail.png", f"{BASE_URL}/admin/payments/{pid}")
                ok.append("payment-detail.png")
            else:
                fail.append(("payment-detail.png", "목록에 결제 없음"))
        except Exception as e:
            fail.append(("payment-detail.png", str(e)))

        # 서비스 상세 + 키 모달 + 카드
        try:
            settle(page, f"{BASE_URL}/admin/services")
            svc = first_id(page, "services")
            if svc:
                # 서비스 상세
                try:
                    shot(page, "service-detail.png", f"{BASE_URL}/admin/services/{svc}")
                    ok.append("service-detail.png")
                except Exception as e:
                    fail.append(("service-detail.png", str(e)))

                # 카드 목록(서비스 상세에 포함) — 상세 화면을 그대로 사용
                # 카드 상세: 서비스 상세 페이지의 카드 링크에서 추출
                try:
                    card_id = first_id(page, "cards")
                    if card_id:
                        shot(page, "card-detail.png", f"{BASE_URL}/admin/cards/{card_id}")
                        ok.append("card-detail.png")
                    else:
                        fail.append(("card-detail.png", "서비스 상세에 카드 링크 없음"))
                except Exception as e:
                    fail.append(("card-detail.png", str(e)))

                # 키/HMAC 모달
                try:
                    shot(page, "service-keys.png", f"{BASE_URL}/admin/services/{svc}/keys-modal")
                    ok.append("service-keys.png")
                except Exception as e:
                    fail.append(("service-keys.png", str(e)))
            else:
                fail.append(("service-detail.png", "목록에 서비스 없음"))
        except Exception as e:
            fail.append(("service-detail.png", str(e)))

        browser.close()

    _summary(ok, fail)


def _summary(ok, fail):
    log("\n==== 캡처 요약 ====")
    log(f"성공 {len(ok)}건: {', '.join(ok) if ok else '-'}")
    if fail:
        log(f"실패/건너뜀 {len(fail)}건:")
        for name, why in fail:
            log(f"  - {name}: {why}")
    log("\n남은 항목(데이터/상태가 필요해 자동화가 까다로운 것):")
    log("  - cards-list.png  : 서비스 상세 화면의 '등록 카드' 영역 (service-detail.png에서 잘라 쓰거나 직접 캡처)")
    log("  - service-keys.png: 실제 시크릿은 키 발급/회전 직후 1회만 노출됩니다(모달이 마스킹일 수 있음)")
    log("필요 시 직접 캡처해 동일 파일명으로 assets/img/ 에 저장하세요.")


if __name__ == "__main__":
    main()
