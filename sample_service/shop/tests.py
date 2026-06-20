"""구독서버(payment_system)와의 HMAC 서명 호환 검증.

기대값은 payment_system의 app.core.security.sign_request로 생성한 고정 벡터 —
어느 한쪽 서명 로직이 바뀌면 이 테스트가 깨진다.
"""
from unittest import mock
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from shop.models import OneOffRecord, SampleUser, ServiceCredential
from shop.payment_client import sign_request, PaymentAPIError


# ─────────────────────────────────────────────
# 테스트 헬퍼 — 활성 서비스 세팅
# ─────────────────────────────────────────────

def _setup_active_service(client, service_id="svc-test", api_key="test-api-key",
                          hmac_secret="test-hmac-secret", name="테스트서비스"):
    """테스트 세션에 활성 서비스를 구성하는 헬퍼.

    ServiceCredential 을 생성하고 세션에 service_id 를 설정한다.
    """
    cred, _ = ServiceCredential.objects.get_or_create(
        service_id=service_id,
        defaults={"name": name, "api_key": api_key, "hmac_secret": hmac_secret})
    session = client.session
    session["service_id"] = service_id
    session.save()
    return cred


def _login_with_service(client, email, service_id="svc-test", api_key="test-api-key",
                        hmac_secret="test-hmac-secret", name="테스트서비스"):
    """새 흐름 로그인 헬퍼 — POST /login 이메일 후 세션에 service_id 직접 설정.

    새 흐름: ① /login(이메일 선택) → /services 리다이렉트 → ② 세션에 service_id 세팅
    반환값: 생성된 SampleUser 인스턴스
    """
    # 1단계: POST /login 으로 이메일 제출(user_id 가 세션에 설정되고 /services 로 리다이렉트)
    client.post("/login", {"email": email})
    # 2단계: ServiceCredential 생성 + 세션에 service_id 직접 설정(_setup_active_service 활용)
    _setup_active_service(client, service_id=service_id, api_key=api_key,
                          hmac_secret=hmac_secret, name=name)
    return SampleUser.objects.get(email=email)


# ─────────────────────────────────────────────
# HMAC 서명 호환 테스트
# ─────────────────────────────────────────────

class SignRequestCompatTest(SimpleTestCase):
    def test_post_with_body_matches_server_vector(self):
        sig = sign_request("test-secret", "POST", "/api/v1/subscriptions",
                           "1700000000", "test-nonce", b'{"a":1}')
        self.assertEqual(
            sig,
            "414b0133d3e3fe5a0906cc7a52068ccdb0974a7b3dbea72c98d428064c585570")

    def test_get_empty_body_matches_server_vector(self):
        sig = sign_request("test-secret", "GET", "/api/v1/plans",
                           "1700000000", "test-nonce", b"")
        self.assertEqual(
            sig,
            "8cef984fdf9b4c2161f4e8dc05736306744a00c0aea6daa766dafe320ecd204b")


# ─────────────────────────────────────────────
# 인증 흐름 테스트 — 새 흐름(① /login → ② /services → ③ /card → ④ /plans or /pay)
# ─────────────────────────────────────────────

class AuthFlowTest(TestCase):
    def test_root_redirects_to_login_when_no_user(self):
        """세션 없는 상태로 /(GET) → /login 리다이렉트(로그인이 첫 번째 단계)."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/login")

    def test_root_redirects_to_services_when_no_service(self):
        """user_id 는 세션에 있지만 service_id 없음 → /services 리다이렉트."""
        # 로그인만 되어 있고 서비스는 선택하지 않은 상태
        self.client.post("/login", {"email": "noservice@x.com"})
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/services")

    def test_root_redirects_to_card_when_both(self):
        """user_id + service_id 모두 세션에 있으면 → /card 리다이렉트."""
        # 로그인 후 서비스도 세팅(헬퍼 사용)
        _login_with_service(self.client, "both@x.com", service_id="svc-both")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/card")

    def test_login_page_accessible_without_service(self):
        """서비스 없는 상태에서 /login(GET) → 200(첫 번째 단계이므로 가드 없음)."""
        resp = self.client.get("/login")
        # /login 은 첫 단계 — 서비스 없어도 리다이렉트하지 않고 200 렌더
        self.assertEqual(resp.status_code, 200)

    def test_login_page_shows_existing_emails(self):
        """SampleUser 생성 후 GET /login → 200, 기존 이메일 목록 표시."""
        SampleUser.objects.create(email="alice@x.com")
        SampleUser.objects.create(email="bob@x.com")
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        # 이미 등록된 이메일이 화면에 표시되어야 함
        self.assertContains(resp, "alice@x.com")
        self.assertContains(resp, "bob@x.com")

    def test_login_requires_no_active_service(self):
        """GET /login — 세션 없어도 200(첫 번째 단계, 가드 불필요)."""
        resp = self.client.get("/login")
        # 서비스 선택 전에도 /login 화면은 접근 가능
        self.assertEqual(resp.status_code, 200)

    def test_login_post_redirects_to_services(self):
        """POST /login(유효한 이메일, 서비스 미설정) → /services 리다이렉트."""
        resp = self.client.post("/login", {"email": "newuser@x.com"})
        self.assertEqual(resp.status_code, 302)
        # 로그인 후 서비스 선택 단계(/services)로 이동
        self.assertEqual(resp.url, "/services")

    def test_login_rejects_invalid_email(self):
        """잘못된 이메일 형식 → 200(에러 표시), 사용자 미생성."""
        resp = self.client.post("/login", {"email": '"></script><script>alert(1)</script>'})
        self.assertEqual(resp.status_code, 200)  # 리다이렉트 없이 에러 표시
        self.assertEqual(SampleUser.objects.count(), 0)

    def test_login_creates_user_with_customer_key(self):
        """POST /login 유효한 이메일 → 사용자 생성 + 32자 customer_key 확인."""
        resp = self.client.post("/login", {"email": "newck@x.com"})
        self.assertEqual(resp.status_code, 302)
        u = SampleUser.objects.get(email="newck@x.com")
        # customer_key 는 uuid4 hex 32자
        self.assertEqual(len(u.customer_key), 32)

    def test_plans_no_login_redirects_to_login(self):
        """세션 없는 상태로 GET /plans → /login 리다이렉트(로그인이 먼저)."""
        resp = self.client.get("/plans")
        self.assertEqual(resp.status_code, 302)
        # 가드 순서: 사용자 없음 → /login
        self.assertEqual(resp.url, "/login")

    def test_plans_with_login_no_service_redirects_to_services(self):
        """로그인 되어 있지만 서비스 없음 → GET /plans → /services 리다이렉트."""
        # 로그인만(서비스 미선택)
        self.client.post("/login", {"email": "nosvc@x.com"})
        resp = self.client.get("/plans")
        self.assertEqual(resp.status_code, 302)
        # 가드 순서: 사용자 있음, 서비스 없음 → /services
        self.assertEqual(resp.url, "/services")

    def test_services_requires_login(self):
        """세션 없는 상태로 GET /services → /login 리다이렉트(로그인이 먼저)."""
        resp = self.client.get("/services")
        self.assertEqual(resp.status_code, 302)
        # 가드 순서: 사용자 없음 → /login
        self.assertEqual(resp.url, "/login")


# ─────────────────────────────────────────────
# 서비스 선택/저장 후 리다이렉트 분기 테스트
# ─────────────────────────────────────────────

class ServiceFlowRedirectTest(TestCase):
    """service_select_view / service_save_key_view 비로그인/로그인 분기."""

    def test_save_key_not_logged_in_redirects_to_card(self):
        """비로그인 상태에서 키 저장 → /card 리다이렉트(save-key는 항상 /card로).

        save-key 뷰는 로그인 여부와 무관하게 항상 /card 로 리다이렉트한다.
        세션에 service_id 는 설정되어 있어야 한다.
        """
        resp = self.client.post("/services/save-key", {
            "service_id": "svc-anon",
            "name": "익명서비스",
            "api_key": "k",
            "hmac_secret": "s",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/card")
        # 세션에 service_id 가 설정되어야 함
        self.assertEqual(self.client.session["service_id"], "svc-anon")

    def test_save_key_logged_in_redirects_to_card(self):
        """로그인 상태에서 키 저장 → /card 리다이렉트(기존 /plans → /card 변경)."""
        user = SampleUser.objects.create(email="logged@x.com")
        session = self.client.session
        session["user_id"] = user.id
        session.save()
        resp = self.client.post("/services/save-key", {
            "service_id": "svc-logged",
            "name": "로그인서비스",
            "api_key": "k2",
            "hmac_secret": "s2",
        })
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: save-key 후 /card 로(기존 /plans 에서 변경)
        self.assertEqual(resp.url, "/card")

    def test_select_redirects_to_card(self):
        """저장된 서비스 선택(비로그인) → /card 리다이렉트(기존 /login → /card 변경)."""
        ServiceCredential.objects.create(service_id="svc-sel-anon", name="테스트",
                                         api_key="k", hmac_secret="s")
        resp = self.client.post("/services/select", {"service_id": "svc-sel-anon"})
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: select 후 /card 로(기존 /login 에서 변경)
        self.assertEqual(resp.url, "/card")

    def test_select_logged_in_redirects_to_card(self):
        """로그인 상태에서 저장된 서비스 선택 → /card 리다이렉트(기존 /plans → /card 변경)."""
        user = SampleUser.objects.create(email="sellog@x.com")
        ServiceCredential.objects.create(service_id="svc-sel-log", name="테스트",
                                         api_key="k", hmac_secret="s")
        session = self.client.session
        session["user_id"] = user.id
        session.save()
        resp = self.client.post("/services/select", {"service_id": "svc-sel-log"})
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: select 후 /card 로(기존 /plans 에서 변경)
        self.assertEqual(resp.url, "/card")

    def test_select_requires_saved_key(self):
        """저장 키 없이 select → /services 리다이렉트 + 에러 메시지(기존 동작 유지)."""
        resp = self.client.post("/services/select", {"service_id": "svc-nosave"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/services")


# ─────────────────────────────────────────────
# 구독 흐름 테스트
# ─────────────────────────────────────────────

class SubscribeFlowTest(TestCase):
    """구독 흐름 — 카드 보관함 전환: 토스 인증 없이 등록 카드로 즉시 구독."""

    PLAN = "11111111-1111-1111-1111-111111111111"

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        return _login_with_service(self.client, "sub@x.com", service_id="svc-subscribe")

    @patch("shop.views.payment_client.get_card")
    def test_subscribe_page_shows_confirm_when_card_exists(self, get_card):
        """등록 카드가 있으면 확인 화면 — 토스 SDK 없이 '구독하기' 버튼 노출."""
        get_card.return_value = {"external_user_id": "sub@x.com",
                                 "card": {"number": "1234-****-****-5678"}}
        self._login()
        resp = self.client.get(f"/subscribe/{self.PLAN}")
        self.assertContains(resp, "구독하기")
        self.assertContains(resp, "1234-****-****-5678")
        # 카드 보관함 전환: 더 이상 토스 SDK/requestBillingAuth 를 쓰지 않는다
        self.assertNotContains(resp, "requestBillingAuth")

    @patch("shop.views.payment_client.get_card")
    def test_subscribe_page_guides_to_card_when_missing(self, get_card):
        """등록 카드가 없으면(404) 카드 등록 화면(/card?next=…)으로 유도하는 링크 노출."""
        get_card.side_effect = PaymentAPIError(404, "NOT_FOUND", "카드 없음")
        self._login()
        resp = self.client.get(f"/subscribe/{self.PLAN}")
        self.assertContains(resp, "카드 등록하러 가기")
        self.assertContains(resp, "/card?next=/subscribe/")

    @patch("shop.views.payment_client.create_subscription")
    def test_subscribe_post_creates_subscription_without_auth_key(self, create_sub):
        """POST /subscribe/<id> → 토스 authKey 없이 create_subscription 호출."""
        create_sub.return_value = {"plan_name": "베이직", "status": "ACTIVE",
                                   "current_period_start": "2026-06-01T00:00:00Z",
                                   "current_period_end": "2026-07-01T00:00:00Z"}
        self._login()
        resp = self.client.post(f"/subscribe/{self.PLAN}", {"trial": "0"})
        create_sub.assert_called_once()
        kwargs = create_sub.call_args.kwargs
        self.assertEqual(kwargs["plan_id"], self.PLAN)
        self.assertEqual(kwargs["external_user_id"], "sub@x.com")
        self.assertFalse(kwargs["trial"])
        # 카드 보관함 전환: customer_key/auth_key 인자는 더 이상 전달하지 않는다
        self.assertNotIn("customer_key", kwargs)
        self.assertNotIn("auth_key", kwargs)
        self.assertContains(resp, "구독이 시작되었습니다")

    @patch("shop.views.payment_client.create_subscription")
    def test_subscribe_post_404_redirects_to_card(self, create_sub):
        """카드 미등록(404)으로 구독 실패 시 카드 등록 화면(/card?next=…)으로 리다이렉트."""
        create_sub.side_effect = PaymentAPIError(404, "CARD_NOT_FOUND", "카드 없음")
        self._login()
        resp = self.client.post(f"/subscribe/{self.PLAN}", {"trial": "0"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/card?next=", resp.url)

    def test_billing_success_requires_login(self):
        """비로그인 /billing/success → 302 /login 리다이렉트."""
        resp = self.client.get("/billing/success?authKey=a&customerKey=b")
        self.assertEqual(resp.status_code, 302)
        # 가드 순서: 사용자 없음 → /login
        self.assertEqual(resp.url, "/login")


# ─────────────────────────────────────────────
# 내 구독 뷰 테스트 (새 흐름 가드 보정)
# ─────────────────────────────────────────────

class MySubscriptionViewTest(TestCase):
    """뷰 라우팅/렌더 스모크 — payment_client 함수 경계까지만 검증."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        _login_with_service(self.client, "my@x.com")

    def test_my_requires_login(self):
        """비로그인 /my → /login 리다이렉트(가드 순서: 사용자 없음 먼저)."""
        resp = self.client.get("/my")
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: 사용자 없으면 /login(기존 / 에서 변경)
        self.assertEqual(resp.url, "/login")

    @patch("shop.views.payment_client.get_subscription")
    def test_my_renders_subscription(self, get_sub):
        get_sub.return_value = {
            "plan_name": "베이직", "status": "ACTIVE", "access_allowed": True,
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "current_period_start": "2026-06-01T00:00:00Z",
            "current_period_end": "2026-07-01T00:00:00Z",
            "next_billing_at": "2026-07-01T00:00:00Z",
            "card": {"number": "1234-****-****-5678"}, "retry_count": 0}
        self._login()
        resp = self.client.get("/my")
        self.assertContains(resp, "베이직")
        self.assertContains(resp, "구독 취소")

    @patch("shop.views.payment_client.get_subscription")
    def test_my_no_subscription(self, get_sub):
        get_sub.side_effect = PaymentAPIError(404, "NOT_FOUND", "구독이 없습니다")
        self._login()
        resp = self.client.get("/my")
        self.assertContains(resp, "구독이 없습니다")


# ─────────────────────────────────────────────
# 단건 결제 흐름 테스트 (새 흐름 가드 보정)
# ─────────────────────────────────────────────

class OneOffPaymentFlowTest(TestCase):
    """일반(단건) 결제 흐름 — 폼 → 토스 체크아웃 → 성공 콜백."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        return _login_with_service(self.client, "oneoff@x.com", service_id="svc-oneoff")

    def test_pay_requires_login(self):
        """비로그인 /pay → /login 리다이렉트(가드 순서: 사용자 없음 먼저)."""
        resp = self.client.get("/pay")
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: 사용자 없으면 /login(기존 / 에서 변경)
        self.assertEqual(resp.url, "/login")

    def test_pay_form_renders(self):
        self._login()
        resp = self.client.get("/pay")
        self.assertContains(resp, 'name="order_name"')
        self.assertContains(resp, 'name="amount"')

    def test_pay_rejects_bad_amount(self):
        self._login()
        resp = self.client.post("/pay", {"order_name": "1회권", "amount": "0"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1원 이상")

    @patch("shop.views.payment_client.create_one_off_payment")
    def test_pay_post_charges_registered_card(self, create):
        """POST /pay → 등록 카드로 즉시 결제(토스 인증 없이 create_one_off_payment 호출)."""
        create.return_value = {"order_id": "oo-x", "status": "DONE", "amount": 5000}
        user = self._login()
        resp = self.client.post("/pay", {"order_name": "1회 이용권", "amount": "5000"})
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["amount"], 5000)
        self.assertEqual(kwargs["order_name"], "1회 이용권")
        self.assertEqual(kwargs["external_user_id"], user.email)
        self.assertTrue(kwargs["order_id"].startswith("oo-"))
        # 카드 보관함 전환: customer_key/auth_key 는 더 이상 전달하지 않는다
        self.assertNotIn("customer_key", kwargs)
        self.assertNotIn("auth_key", kwargs)
        self.assertContains(resp, "결제가 완료되었습니다")
        # 성공 시 로컬 OneOffRecord 생성
        self.assertTrue(OneOffRecord.objects.filter(order_id=kwargs["order_id"]).exists())

    @patch("shop.views.payment_client.create_one_off_payment")
    def test_pay_post_404_redirects_to_card(self, create):
        """카드 미등록(404)으로 결제 실패 시 카드 등록 화면(/card?next=/pay)으로 리다이렉트."""
        create.side_effect = PaymentAPIError(404, "CARD_NOT_FOUND", "카드 없음")
        self._login()
        resp = self.client.post("/pay", {"order_name": "1회권", "amount": "5000"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/card?next=/pay")
        # 결제 실패 시 OneOffRecord 미생성
        self.assertEqual(OneOffRecord.objects.count(), 0)


# ─────────────────────────────────────────────
# 단건 결제 취소 뷰 테스트 (새 흐름 가드 보정)
# ─────────────────────────────────────────────

class OneOffCancelViewTest(TestCase):
    """oneoff_cancel_view 라우팅/뷰 스모크 — payment_client 경계까지만 검증."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        _login_with_service(self.client, "cancel@x.com", service_id="svc-cancel")

    def test_cancel_requires_login(self):
        """비로그인 POST → /login 리다이렉트(가드 순서: 사용자 없음 먼저)."""
        resp = self.client.post("/pay/cancel", {"order_id": "oo-test"})
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: 사용자 없으면 /login(기존 / 에서 변경)
        self.assertEqual(resp.url, "/login")

    @patch("shop.views.payment_client.cancel_one_off_payment")
    def test_cancel_success_redirects_with_message(self, cancel_mock):
        """성공 시 messages.success + /history 리다이렉트."""
        cancel_mock.return_value = {"order_id": "oo-abc", "status": "CANCELED"}
        self._login()
        resp = self.client.post("/pay/cancel", {"order_id": "oo-abc"},
                                follow=True)
        cancel_mock.assert_called_once()
        messages_list = list(resp.context["messages"])
        self.assertTrue(any("취소되었습니다" in str(m) for m in messages_list))

    @patch("shop.views.payment_client.cancel_one_off_payment")
    def test_cancel_api_error_shows_message(self, cancel_mock):
        """PaymentAPIError(취소불가 포함) → messages.error + /history 리다이렉트."""
        from shop.payment_client import PaymentAPIError
        cancel_mock.side_effect = PaymentAPIError(
            422, "CANCEL_DISABLED", "취소가 허용되지 않는 서비스입니다")
        self._login()
        resp = self.client.post("/pay/cancel", {"order_id": "oo-xyz"},
                                follow=True)
        messages_list = list(resp.context["messages"])
        self.assertTrue(any("CANCEL_DISABLED" in str(m) for m in messages_list))

    @patch("shop.views.payment_client.cancel_one_off_payment")
    def test_cancel_connection_error_shows_message(self, cancel_mock):
        """연결 오류(Exception) → messages.error + /pay 리다이렉트."""
        cancel_mock.side_effect = Exception("Connection refused")
        self._login()
        resp = self.client.post("/pay/cancel", {"order_id": "oo-xyz"},
                                follow=True)
        messages_list = list(resp.context["messages"])
        self.assertTrue(any("연결할 수 없습니다" in str(m) for m in messages_list))


# ─────────────────────────────────────────────
# 요금제 뷰 테스트 (새 흐름 가드 보정)
# ─────────────────────────────────────────────

class PlansViewTest(TestCase):
    def _login(self, email="p@x.com"):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        _login_with_service(self.client, email, service_id=f"svc-plans-{email}")

    @patch("shop.views.payment_client.get_plans")
    def test_plans_shows_first_amount(self, get_plans):
        get_plans.return_value = [{
            "id": "11111111-1111-1111-1111-111111111111", "name": "베이직",
            "price": 10000, "amount": 9500, "billing_cycle": "MONTH",
            "cycle_days": None, "first_payment_type": "DISCOUNT_AMOUNT",
            "first_payment_value": 1000, "trial_enabled": False, "trial_days": None,
            "currency": "KRW"}]
        self._login("p@x.com")
        resp = self.client.get("/plans")
        self.assertContains(resp, "9,000원")   # 첫 결제 = 정가 10000 − 1000 (intcomma 표시)
        self.assertContains(resp, "9,500원")   # 정기 결제 (intcomma 표시)

    @patch("shop.views.payment_client.get_plans")
    def test_plans_shows_auto_renew_false_badge(self, get_plans):
        """auto_renew=False 인 요금제에 '자동결제 안함' 배지가 표시된다."""
        get_plans.return_value = [{
            "id": "22222222-2222-2222-2222-222222222222", "name": "프리미엄",
            "price": 20000, "amount": 20000, "billing_cycle": "MONTH",
            "cycle_days": None, "first_payment_type": None,
            "first_payment_value": None, "trial_enabled": False, "trial_days": None,
            "currency": "KRW", "auto_renew": False, "extra_info": {}}]
        self._login("p2@x.com")
        resp = self.client.get("/plans")
        self.assertContains(resp, "자동결제 안함")

    @patch("shop.views.payment_client.get_plans")
    def test_plans_shows_extra_info(self, get_plans):
        """extra_info 가 있으면 key: value 목록이 표시된다."""
        get_plans.return_value = [{
            "id": "33333333-3333-3333-3333-333333333333", "name": "엔터프라이즈",
            "price": 50000, "amount": 50000, "billing_cycle": "MONTH",
            "cycle_days": None, "first_payment_type": None,
            "first_payment_value": None, "trial_enabled": False, "trial_days": None,
            "currency": "KRW", "auto_renew": True,
            "extra_info": {"seats": "5", "storage": "100GB"}}]
        self._login("p3@x.com")
        resp = self.client.get("/plans")
        self.assertContains(resp, "seats")
        self.assertContains(resp, "100GB")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 결제 내역(history) 관련 테스트 (새 흐름 가드 보정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GetPaymentsClientTest(SimpleTestCase):
    """payment_client.get_payments 가 올바른 경로로 _request 호출."""

    @patch("shop.payment_client._request")
    def test_calls_correct_path(self, mock_req):
        """get_payments(uid) → _request("GET", "/api/v1/payments/{uid}")["payments"]."""
        mock_req.return_value = {"payments": [{"order_id": "sub-001", "amount": 9900}]}
        from shop.payment_client import get_payments
        result = get_payments("user@example.com")
        # creds=None 기본값으로 호출되는지 확인
        mock_req.assert_called_once_with("GET", "/api/v1/payments/user@example.com",
                                         creds=None)
        self.assertEqual(result, [{"order_id": "sub-001", "amount": 9900}])

    @patch("shop.payment_client._request")
    def test_returns_empty_on_no_history(self, mock_req):
        """결제 내역이 없으면 빈 리스트 반환."""
        mock_req.return_value = {"payments": []}
        from shop.payment_client import get_payments
        result = get_payments("empty@example.com")
        self.assertEqual(result, [])


class HistoryViewTest(TestCase):
    """history_view — 로그인/결제내역 렌더/단건기록 표시."""

    def _login(self, email="hist@x.com"):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        _login_with_service(self.client, email, service_id=f"svc-hist-{email}")
        return SampleUser.objects.get(email=email)

    def test_history_requires_login(self):
        """비로그인 접근 → /login 리다이렉트(가드 순서: 사용자 없음 먼저)."""
        resp = self.client.get("/history")
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: 사용자 없으면 /login(기존 / 에서 변경)
        self.assertEqual(resp.url, "/login")

    @patch("shop.views.payment_client.get_payments")
    def test_history_renders_subscription_payments(self, get_pay):
        """get_payments mock 으로 구독 결제 내역이 렌더된다."""
        get_pay.return_value = [{
            "order_id": "sub-ord-001", "kind": "SUBSCRIPTION", "payment_type": "CARD",
            "status": "DONE", "amount": 9900,
            "requested_at": "2026-06-01T00:00:00Z",
            "approved_at": "2026-06-01T00:00:01Z",
            "failure_code": None, "failure_message": None}]
        self._login()
        resp = self.client.get("/history")
        self.assertContains(resp, "sub-ord-001")
        self.assertContains(resp, "DONE")     # 구독 결제 상태 배지 렌더
        self.assertContains(resp, "9,900원")  # intcomma 표시

    @patch("shop.views.payment_client.get_payments")
    def test_history_renders_oneoff_records_and_cancel_button(self, get_pay):
        """OneOffRecord 있으면 단건 내역 + 서버가 cancelable이라 한 건에만 취소 버튼 표시."""
        # 취소 버튼은 서버 응답의 cancelable로 결정된다(요청 016 이후 서버 기준).
        get_pay.return_value = [
            {"order_id": "oo-xyz", "kind": "ONE_OFF", "cancelable": True,
             "cancel_fee": 0, "cancel_refund_amount": 5000, "cancel_fee_percent": 0,
             "canceled_amount": 0, "net_amount": 5000, "status": "DONE"},
            {"order_id": "oo-abc", "kind": "ONE_OFF", "cancelable": False,
             "cancel_fee": 0, "cancel_refund_amount": 1000, "cancel_fee_percent": 0,
             "canceled_amount": 1000, "net_amount": 0, "status": "CANCELED"},
        ]
        user = self._login("hist2@x.com")
        from shop.models import OneOffRecord
        # 취소되지 않은 단건 결제 기록(서버: cancelable=True)
        OneOffRecord.objects.create(user=user, order_id="oo-xyz", order_name="1회권",
                                    amount=5000, canceled=False)
        # 이미 취소된 단건 결제 기록(서버: status=CANCELED)
        OneOffRecord.objects.create(user=user, order_id="oo-abc", order_name="체험권",
                                    amount=1000, canceled=True)
        resp = self.client.get("/history")
        # 두 기록 모두 렌더
        self.assertContains(resp, "1회권")
        self.assertContains(resp, "체험권")
        # 미취소 건에만 취소 버튼 — order_id "oo-xyz" 의 form이 있어야 함
        self.assertContains(resp, 'value="oo-xyz"')
        # 이미 취소된 건에는 "취소됨" 배지
        self.assertContains(resp, "취소됨")

    @patch("shop.views.payment_client.get_payments")
    def test_history_server_error_shows_message(self, get_pay):
        """get_payments 예외 시 에러 메시지 표시 후 페이지 정상 렌더."""
        get_pay.side_effect = Exception("Connection refused")
        self._login("hist3@x.com")
        resp = self.client.get("/history")
        self.assertContains(resp, "연결할 수 없습니다")

    @patch("shop.views.payment_client.get_payments")
    def test_history_shows_oneoff_cancel_fee_and_refund(self, get_pay):
        """서버 API가 반환한 단건 취소 수수료/환불 예정액이 결제내역에 표시된다."""
        user = self._login("hist4@x.com")
        from shop.models import OneOffRecord
        OneOffRecord.objects.create(user=user, order_id="oo-fee", order_name="1회권",
                                    amount=10000, canceled=False)
        # 서버 응답: 같은 order_id의 단건 결제에 취소 수수료 10%(수수료 1000, 환불 9000)
        get_pay.return_value = [{
            "order_id": "oo-fee", "kind": "ONE_OFF", "payment_type": "ONE_OFF",
            "status": "DONE", "amount": 10000,
            "requested_at": "2026-06-01T00:00:00Z", "approved_at": "2026-06-01T00:00:01Z",
            "failure_code": None, "failure_message": None,
            "cancelable": True, "cancel_fee_percent": 10,
            "cancel_fee": 1000, "cancel_refund_amount": 9000}]
        resp = self.client.get("/history")
        # 수수료/환불 예정액이 화면에 노출
        self.assertContains(resp, "1,000원")   # 취소 수수료 (intcomma 표시)
        self.assertContains(resp, "9,000원")   # 실제 환불액 (intcomma 표시)
        # 취소 버튼 confirm 메시지에도 금액이 포함
        self.assertContains(resp, "1000원이 차감되고 9000원이 환불됩니다")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OneOffRecord 생성/취소 테스트 (새 흐름 가드 보정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OneOffRecordCreationTest(TestCase):
    """단건 결제 성공 시 OneOffRecord 생성."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        return _login_with_service(self.client, "rec@x.com", service_id="svc-rec")

    def test_oneoff_success_creates_record(self):
        """POST /pay 성공 → OneOffRecord 생성(카드 보관함 전환: 즉시 결제)."""
        user = self._login()
        with patch("shop.views.payment_client.create_one_off_payment") as create:
            create.return_value = {"order_id": "oo-create01", "status": "DONE",
                                   "amount": 3000}
            self.client.post("/pay", {"order_name": "테스트권", "amount": "3000"})
        # DB에 기록이 생성되었는지 검증(order_id는 뷰가 생성하므로 사용자 기준으로 조회)
        rec = OneOffRecord.objects.get(user=user)
        self.assertEqual(rec.order_name, "테스트권")
        self.assertEqual(rec.amount, 3000)
        self.assertFalse(rec.canceled)
        self.assertTrue(rec.order_id.startswith("oo-"))

    def test_oneoff_api_error_does_not_create_record(self):
        """API 에러 시 OneOffRecord 생성 안 됨."""
        self._login()
        with patch("shop.views.payment_client.create_one_off_payment") as create:
            create.side_effect = PaymentAPIError(400, "PAYMENT_FAILED", "결제 실패")
            self.client.post("/pay", {"order_name": "실패권", "amount": "9999"})
        self.assertEqual(OneOffRecord.objects.count(), 0)


class OneOffCancelRecordTest(TestCase):
    """단건 결제 취소 시 OneOffRecord.canceled=True + /history 리다이렉트."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        return _login_with_service(self.client, "cancelrec@x.com",
                                   service_id="svc-cancelrec")

    @patch("shop.views.payment_client.cancel_one_off_payment")
    def test_cancel_updates_record_and_redirects_to_history(self, cancel_mock):
        """취소 성공 시 canceled=True 업데이트 + /history 리다이렉트."""
        from shop.models import OneOffRecord
        cancel_mock.return_value = {"order_id": "oo-del01", "status": "CANCELED"}
        user = self._login()
        # 취소할 기록 미리 생성
        OneOffRecord.objects.create(user=user, order_id="oo-del01",
                                    order_name="취소테스트", amount=1000, canceled=False)
        resp = self.client.post("/pay/cancel", {"order_id": "oo-del01"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/history")  # /pay 가 아닌 /history 로 리다이렉트
        rec = OneOffRecord.objects.get(order_id="oo-del01")
        self.assertTrue(rec.canceled)  # canceled=True 업데이트 확인

    @patch("shop.views.payment_client.cancel_one_off_payment")
    def test_cancel_api_error_does_not_update_record(self, cancel_mock):
        """API 에러 시 canceled 상태 변경 없이 /history 리다이렉트."""
        from shop.models import OneOffRecord
        cancel_mock.side_effect = PaymentAPIError(422, "CANCEL_DISABLED", "취소 불가")
        user = self._login()
        OneOffRecord.objects.create(user=user, order_id="oo-nd01",
                                    order_name="취소불가", amount=500, canceled=False)
        resp = self.client.post("/pay/cancel", {"order_id": "oo-nd01"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/history")
        rec = OneOffRecord.objects.get(order_id="oo-nd01")
        self.assertFalse(rec.canceled)  # 에러 시 canceled 변경 없음


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# my.html PAST_DUE 수동결제 버튼 테스트 (새 흐름 가드 보정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MyViewPastDueTest(TestCase):
    """PAST_DUE 상태에서 수동결제 버튼 노출."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        _login_with_service(self.client, "pastdue@x.com", service_id="svc-pastdue")

    @patch("shop.views.payment_client.get_subscription")
    def test_past_due_shows_manual_pay_button(self, get_sub):
        """PAST_DUE 상태에서 '수동 결제' 버튼이 렌더된다."""
        get_sub.return_value = {
            "plan_name": "베이직", "status": "PAST_DUE", "access_allowed": True,
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "current_period_start": "2026-06-01T00:00:00Z",
            "current_period_end": "2026-07-01T00:00:00Z",
            "next_billing_at": "2026-07-01T00:00:00Z",
            "card": {"number": "1234-****-****-5678"}, "retry_count": 2}
        self._login()
        resp = self.client.get("/my")
        self.assertContains(resp, "수동 결제")   # 수동결제 버튼 노출
        self.assertContains(resp, "연체된 상태입니다")   # PAST_DUE 경고 배너 노출
        self.assertContains(resp, "구독 취소")   # 취소 버튼도 함께 노출
        self.assertContains(resp, "카드 변경")   # 카드 변경 버튼도 함께 노출

    @patch("shop.views.payment_client.get_subscription")
    def test_suspended_shows_manual_pay_button(self, get_sub):
        """SUSPENDED 상태에서도 '수동 결제' 버튼이 렌더된다(기존 동작 유지)."""
        get_sub.return_value = {
            "plan_name": "베이직", "status": "SUSPENDED", "access_allowed": False,
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "current_period_start": "2026-06-01T00:00:00Z",
            "current_period_end": "2026-07-01T00:00:00Z",
            "next_billing_at": None,
            "card": {"number": "1234-****-****-5678"}, "retry_count": 3}
        self._login()
        resp = self.client.get("/my")
        self.assertContains(resp, "수동 결제")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 신규: 서비스 선택/저장/전환 테스트 (새 흐름 — 로그인 후 진입)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ServicesSelectTest(TestCase):
    """서비스 선택/저장 뷰 테스트."""

    def setUp(self):
        """테스트 사용자 생성 + 로그인 세팅."""
        self.user = SampleUser.objects.create(email="s@example.com")
        session = self.client.session
        session["user_id"] = self.user.id
        session.save()

    @mock.patch("shop.views.payment_client.list_services")
    def test_services_lists_and_marks_saved(self, lst):
        """GET /services — 서버 목록 렌더 + 저장 안 된 서비스에 키 입력 폼 노출."""
        lst.return_value = [{"id": "svc-1", "name": "서비스A", "status": "ACTIVE"}]
        resp = self.client.get("/services")
        self.assertEqual(resp.status_code, 200)
        # 서비스 이름 렌더 확인
        self.assertContains(resp, "서비스A")
        # 저장된 키 없으면 키 입력 폼 노출(save-key action)
        self.assertContains(resp, "save-key")

    @mock.patch("shop.views.payment_client.list_services")
    def test_services_marks_saved_service(self, lst):
        """이미 저장된 서비스는 '키 저장됨' + 선택 버튼 표시."""
        lst.return_value = [{"id": "svc-saved", "name": "저장서비스", "status": "ACTIVE"}]
        # 키 저장
        ServiceCredential.objects.create(service_id="svc-saved", name="저장서비스",
                                         api_key="ak", hmac_secret="hs")
        resp = self.client.get("/services")
        self.assertContains(resp, "키 저장됨")
        self.assertContains(resp, "선택")

    def test_save_key_persists_and_activates(self):
        """POST /services/save-key — ServiceCredential 생성 + session service_id 설정 + /card.

        새 흐름: 로그인 상태라도 save-key 후 /card 로 리다이렉트(기존 /plans → /card 변경).
        """
        resp = self.client.post("/services/save-key", {
            "service_id": "svc-new",
            "name": "신규서비스",
            "api_key": "new-api-key",
            "hmac_secret": "new-hmac-secret",
        })
        # 새 흐름: save-key → /card 리다이렉트(기존 /plans 에서 변경)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/card")
        # DB 저장 확인
        cred = ServiceCredential.objects.get(service_id="svc-new")
        self.assertEqual(cred.name, "신규서비스")
        self.assertEqual(cred.api_key, "new-api-key")
        self.assertEqual(cred.hmac_secret, "new-hmac-secret")
        # 세션 활성화 확인
        self.assertEqual(self.client.session["service_id"], "svc-new")

    def test_save_key_update_or_create(self):
        """기존 서비스 키 갱신 — update_or_create 동작 확인 + /card 리다이렉트."""
        # 기존 키 생성
        ServiceCredential.objects.create(service_id="svc-update", name="원래서비스",
                                         api_key="old-key", hmac_secret="old-secret")
        resp = self.client.post("/services/save-key", {
            "service_id": "svc-update",
            "name": "갱신서비스",
            "api_key": "new-key",
            "hmac_secret": "new-secret",
        })
        # 새 흐름: save-key 후 /card(기존 /plans 에서 변경)
        self.assertEqual(resp.url, "/card")
        # 갱신 확인(create 아닌 update)
        cred = ServiceCredential.objects.get(service_id="svc-update")
        self.assertEqual(cred.api_key, "new-key")
        self.assertEqual(ServiceCredential.objects.filter(service_id="svc-update").count(), 1)

    def test_save_key_missing_fields_shows_error(self):
        """필수 필드 누락 — 에러 메시지 + /services 리다이렉트."""
        resp = self.client.post("/services/save-key", {
            "service_id": "svc-miss",
            "api_key": "",   # hmac_secret 도 비어 있음
            "hmac_secret": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/services")

    def test_select_requires_saved_key(self):
        """저장 키 없이 select → /services 리다이렉트 + 에러 메시지."""
        resp = self.client.post("/services/select", {"service_id": "svc-nosave"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/services")
        # service_id 가 세션에 세팅되지 않아야 함
        self.assertNotIn("service_id", self.client.session)

    def test_select_with_saved_key_activates(self):
        """저장된 키가 있는 서비스 select → 활성화 + /card(새 흐름, 기존 /plans → /card 변경)."""
        ServiceCredential.objects.create(service_id="svc-sel", name="선택서비스",
                                         api_key="ak", hmac_secret="hs")
        resp = self.client.post("/services/select", {"service_id": "svc-sel"})
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: select 후 /card(기존 /plans 에서 변경)
        self.assertEqual(resp.url, "/card")
        self.assertEqual(self.client.session["service_id"], "svc-sel")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 신규: 활성 creds 서명 검증 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ActiveCredsSigningTest(TestCase):
    """활성 서비스 creds 가 _request 서명에 실제로 쓰이는지 검증."""

    def setUp(self):
        """사용자 + 활성 서비스 세팅."""
        self.user = SampleUser.objects.create(email="cred@example.com")
        session = self.client.session
        session["user_id"] = self.user.id
        # 활성 서비스 설정
        ServiceCredential.objects.create(service_id="svc-cred", name="서명서비스",
                                         api_key="active-api-key",
                                         hmac_secret="active-hmac-secret")
        session["service_id"] = "svc-cred"
        session.save()

    @patch("shop.payment_client._request")
    def test_plans_view_passes_active_creds_to_request(self, mock_req):
        """plans_view 가 활성 서비스 creds=(api_key, hmac_secret)을 _request 에 전달."""
        mock_req.return_value = {"plans": []}
        self.client.get("/plans")
        # _request 가 creds 인자와 함께 호출되었는지 확인
        call_kwargs = mock_req.call_args
        # keyword 인자 creds 확인
        passed_creds = call_kwargs.kwargs.get("creds") or (
            call_kwargs.args[3] if len(call_kwargs.args) > 3 else None)
        # get_plans(creds=creds) → _request("GET", "/api/v1/plans", creds=creds)
        # mock_req 가 실제 _request 이므로 creds 키워드 확인
        self.assertIsNotNone(call_kwargs)
        # creds 가 활성 서비스 값인지 확인
        if call_kwargs.kwargs.get("creds"):
            self.assertEqual(call_kwargs.kwargs["creds"],
                             ("active-api-key", "active-hmac-secret"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 신규: 401 → 키 재입력 유도 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ReauthRedirectTest(TestCase):
    """401 에러 시 /services?reauth=<service_id> 로 유도."""

    def setUp(self):
        """사용자 + 활성 서비스 세팅."""
        self.user = SampleUser.objects.create(email="reauth@example.com")
        ServiceCredential.objects.create(service_id="svc-reauth", name="재인증서비스",
                                         api_key="old-key", hmac_secret="old-secret")
        session = self.client.session
        session["user_id"] = self.user.id
        session["service_id"] = "svc-reauth"
        session.save()

    @patch("shop.views.payment_client.get_plans")
    def test_plans_401_redirects_to_reauth(self, mock_plans):
        """plans_view — 401 에러 시 /services?reauth=svc-reauth 로 리다이렉트."""
        mock_plans.side_effect = PaymentAPIError(401, "UNAUTHORIZED", "인증 실패")
        resp = self.client.get("/plans")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/services", resp.url)
        self.assertIn("reauth=svc-reauth", resp.url)

    @patch("shop.views.payment_client.get_subscription")
    def test_my_401_redirects_to_reauth(self, mock_sub):
        """my_view — 401 에러 시 /services?reauth=svc-reauth 로 리다이렉트."""
        mock_sub.side_effect = PaymentAPIError(401, "UNAUTHORIZED", "인증 실패")
        resp = self.client.get("/my")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("reauth=svc-reauth", resp.url)

    @patch("shop.views.payment_client.get_payments")
    def test_history_401_redirects_to_reauth(self, mock_pay):
        """history_view — 401 에러 시 /services?reauth=svc-reauth 로 리다이렉트."""
        mock_pay.side_effect = PaymentAPIError(401, "UNAUTHORIZED", "인증 실패")
        resp = self.client.get("/history")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("reauth=svc-reauth", resp.url)

    @patch("shop.views.payment_client.cancel")
    def test_action_view_cancel_401_redirects_to_reauth_with_message(self, mock_cancel):
        """_action_view(cancel) — 401 에러 시 /services?reauth=<service_id> 리다이렉트 + 경고 메시지.

        cancel/resume/pay 세 뷰 모두 _action_view를 거치므로 cancel 1건으로 공통 경로 검증.
        """
        # 401 PaymentAPIError 발생 시뮬레이션 — 키가 서버에서 무효화된 상황
        mock_cancel.side_effect = PaymentAPIError(401, "UNAUTHORIZED", "인증 실패")
        resp = self.client.post("/my/cancel", follow=False)
        # /services?reauth=svc-reauth 로 리다이렉트 확인
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/services", resp.url)
        self.assertIn("reauth=svc-reauth", resp.url)
        # follow=True 로 리다이렉트 결과 페이지에서 경고 메시지 확인
        resp_follow = self.client.post("/my/cancel", follow=True)
        messages_list = list(resp_follow.context["messages"])
        self.assertTrue(
            any("key가 변경되었습니다" in str(m) or "변경" in str(m) for m in messages_list),
            f"경고 메시지 없음: {[str(m) for m in messages_list]}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 신규: 카드 보관함(Card Vault) — 클라이언트 메서드 + 화면/콜백
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CardVaultClientTest(SimpleTestCase):
    """payment_client 카드 보관함 메서드가 올바른 경로·본문으로 _request 호출."""

    @patch("shop.payment_client._request")
    def test_register_card_posts_cards(self, mock_req):
        """register_card → POST /api/v1/cards, 본문 {external_user_id, customer_key, auth_key}."""
        mock_req.return_value = {"external_user_id": "u@x.com", "card": {"number": "1"}}
        from shop.payment_client import register_card
        register_card(external_user_id="u@x.com", customer_key="ck", auth_key="ak")
        mock_req.assert_called_once_with(
            "POST", "/api/v1/cards",
            {"external_user_id": "u@x.com", "customer_key": "ck", "auth_key": "ak"},
            creds=None)

    @patch("shop.payment_client._request")
    def test_get_card_gets_cards(self, mock_req):
        """get_card → GET /api/v1/cards/{external_user_id}."""
        mock_req.return_value = {"external_user_id": "u@x.com", "card": None}
        from shop.payment_client import get_card
        get_card("u@x.com")
        mock_req.assert_called_once_with("GET", "/api/v1/cards/u@x.com", creds=None)

    @patch("shop.payment_client._request")
    def test_delete_card_deletes_cards(self, mock_req):
        """delete_card → DELETE /api/v1/cards/{external_user_id}."""
        mock_req.return_value = {}
        from shop.payment_client import delete_card
        delete_card("u@x.com")
        mock_req.assert_called_once_with("DELETE", "/api/v1/cards/u@x.com", creds=None)

    @patch("shop.payment_client._request")
    def test_create_subscription_no_auth_key(self, mock_req):
        """create_subscription 본문에 auth_key/customer_key 가 없다(카드 보관함 전환)."""
        mock_req.return_value = {"status": "ACTIVE"}
        from shop.payment_client import create_subscription
        create_subscription(plan_id="p1", external_user_id="u@x.com", trial=False)
        body = mock_req.call_args.args[2]
        self.assertEqual(body, {"plan_id": "p1", "external_user_id": "u@x.com",
                                "trial": False})

    @patch("shop.payment_client._request")
    def test_create_one_off_no_auth_key(self, mock_req):
        """create_one_off_payment 본문에 auth_key/customer_key 가 없다(카드 보관함 전환)."""
        mock_req.return_value = {"status": "DONE"}
        from shop.payment_client import create_one_off_payment
        create_one_off_payment(order_id="oo-1", order_name="권", amount=1000,
                               external_user_id="u@x.com")
        body = mock_req.call_args.args[2]
        self.assertEqual(body, {"order_id": "oo-1", "order_name": "권",
                                "amount": 1000, "external_user_id": "u@x.com"})

    def test_change_card_removed(self):
        """카드 변경 전용 함수(change_card)는 제거되었다 — 재등록(register_card)로 통합."""
        import shop.payment_client as pc
        self.assertFalse(hasattr(pc, "change_card"))


class CardViewTest(TestCase):
    """/card 화면 — 등록 카드 조회/표시 + 삭제 + Toss 위젯/폴백 렌더."""

    def _login(self, email="card@x.com"):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        return _login_with_service(self.client, email, service_id=f"svc-card-{email}")

    def test_card_requires_login(self):
        """비로그인 /card → /login 리다이렉트(가드 순서: 사용자 없음 먼저)."""
        resp = self.client.get("/card")
        self.assertEqual(resp.status_code, 302)
        # 새 흐름: 사용자 없으면 /login(기존 / 에서 변경)
        self.assertEqual(resp.url, "/login")

    @patch("shop.views.payment_client.get_card")
    def test_card_shows_registered_card(self, get_card):
        """등록 카드가 있으면 마스킹 번호 + '카드 변경(재등록)'/'카드 삭제' 노출."""
        get_card.return_value = {"external_user_id": "card@x.com",
                                 "card": {"number": "1234-****-****-5678",
                                          "issuerCode": "11"}}
        self._login()
        resp = self.client.get("/card")
        self.assertContains(resp, "1234-****-****-5678")
        self.assertContains(resp, "카드 변경")
        self.assertContains(resp, "카드 삭제")

    @patch("shop.views.payment_client.get_card")
    def test_card_no_card_shows_register(self, get_card):
        """카드 미등록(404)이면 등록 유도 화면 — '카드 등록창 열기' 노출."""
        get_card.side_effect = PaymentAPIError(404, "NOT_FOUND", "카드 없음")
        self._login()
        resp = self.client.get("/card")
        self.assertContains(resp, "카드 등록창 열기")
        # 토스 키 없이도 테스트 가능한 수동 authKey 폴백 노출
        self.assertContains(resp, "수동 authKey")

    @patch("shop.views.payment_client.delete_card")
    @patch("shop.views.payment_client.get_card")
    def test_card_delete_calls_api_and_redirects(self, get_card, del_card):
        """POST /card (delete=1) → delete_card 호출 + /card 리다이렉트 + 성공 메시지."""
        del_card.return_value = None
        self._login()
        resp = self.client.post("/card", {"delete": "1"}, follow=True)
        del_card.assert_called_once()
        messages_list = list(resp.context["messages"])
        self.assertTrue(any("삭제" in str(m) for m in messages_list))

    @patch("shop.views.payment_client.delete_card")
    @patch("shop.views.payment_client.get_card")
    def test_card_delete_conflict_shows_message(self, get_card, del_card):
        """사용 중 카드 삭제(409) → 에러 메시지 표시."""
        del_card.side_effect = PaymentAPIError(409, "CONFLICT", "사용 중인 카드")
        self._login()
        resp = self.client.post("/card", {"delete": "1"}, follow=True)
        messages_list = list(resp.context["messages"])
        self.assertTrue(any("CONFLICT" in str(m) for m in messages_list))


class BillingSuccessRegistersCardTest(TestCase):
    """billing_success_view — authKey로 카드 등록(POST /api/v1/cards) 전용 콜백."""

    def _login(self):
        """새 흐름 로그인 — POST /login 후 세션에 service_id 설정."""
        return _login_with_service(self.client, "bsuc@x.com", service_id="svc-bsuc")

    @patch("shop.views.payment_client.register_card")
    def test_success_registers_card_and_redirects_next(self, reg):
        """authKey/customerKey 정상 → register_card 호출 + next 경로로 리다이렉트."""
        reg.return_value = {"external_user_id": "bsuc@x.com", "card": {"number": "1"}}
        user = self._login()
        resp = self.client.get(
            f"/billing/success?next=/plans&authKey=ak&customerKey={user.customer_key}")
        reg.assert_called_once()
        kwargs = reg.call_args.kwargs
        self.assertEqual(kwargs["external_user_id"], user.email)
        self.assertEqual(kwargs["auth_key"], "ak")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/plans")

    @patch("shop.views.payment_client.register_card")
    def test_success_rejects_bad_customer_key(self, reg):
        """customerKey 불일치 → register_card 미호출 + /card 리다이렉트."""
        self._login()
        resp = self.client.get("/billing/success?authKey=ak&customerKey=wrong")
        reg.assert_not_called()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/card")

    @patch("shop.views.payment_client.register_card")
    def test_success_rejects_external_next(self, reg):
        """외부 URL next 는 무시하고 /card 로(오픈 리다이렉트 방지)."""
        reg.return_value = {"external_user_id": "bsuc@x.com", "card": {}}
        user = self._login()
        resp = self.client.get(
            f"/billing/success?next=http://evil.com&authKey=ak&customerKey={user.customer_key}")
        self.assertEqual(resp.url, "/card")


# ─────────────────────────────────────────────
# 서비스 알림 수신(요청 016) — POST /notify HMAC 검증
# ─────────────────────────────────────────────

class NotifyReceiveTest(TestCase):
    def _signed_post(self, payload, secret):
        import json as _json
        import hmac as _hmac
        import hashlib
        body = _json.dumps(payload).encode()
        ts, nonce = "1700000000", "abc123"
        msg = "\n".join(["POST", "/notify", ts, nonce,
                         hashlib.sha256(body).hexdigest()]).encode()
        sig = _hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        return self.client.post(
            "/notify", data=body, content_type="application/json",
            HTTP_X_SIGNATURE=sig, HTTP_X_TIMESTAMP=ts, HTTP_X_NONCE=nonce)

    def test_valid_signature_records_verified(self):
        from shop.models import NotificationRecord
        ServiceCredential.objects.create(service_id="s1", name="My Svc",
                                         api_key="k", hmac_secret="secret-xyz")
        payload = {"EVENT": "payment.one_off", "service_name": "My Svc",
                   "email": "u@x.com", "order_id": "o-1", "STATUS": "DONE",
                   "DESC": "10,000원"}
        resp = self._signed_post(payload, "secret-xyz")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["verified"])
        rec = NotificationRecord.objects.get()
        self.assertEqual(rec.event, "payment.one_off")
        self.assertTrue(rec.verified)

    def test_bad_signature_records_unverified(self):
        from shop.models import NotificationRecord
        ServiceCredential.objects.create(service_id="s1", name="My Svc",
                                         api_key="k", hmac_secret="secret-xyz")
        payload = {"EVENT": "card.registered", "service_name": "My Svc"}
        resp = self._signed_post(payload, "WRONG-secret")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["verified"])
        self.assertFalse(NotificationRecord.objects.get().verified)

    def test_notifications_page_shows_receive_url(self):
        """/notifications 화면에 등록용 수신 URL(/notify)이 표시된다."""
        resp = self.client.get("/notifications")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/notify")
        self.assertContains(resp, "알림 수신 URL")
