"""서비스 알림 URL 어드민 저장 + 상세 화면 노출 e2e (요청 016)."""
from app.models import Service
from tests.factories import create_service, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_service_detail_shows_notification_card(client, db, redis_client, cipher):
    """서비스 상세에 '서비스 알림 URL' 입력 카드 + 이벤트 설명 모달이 노출된다."""
    svc, _, _ = await create_service(db, cipher, name="notify-card-svc")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "서비스 알림 URL" in html
    assert 'name="notification_url"' in html
    assert "서비스 알림 이벤트" in html        # 설명 모달 제목


async def test_save_notification_url(client, db, redis_client, cipher):
    """알림 URL 저장 → DB 반영. 빈값 저장 시 NULL(끔). 잘못된 형식은 거부."""
    svc, _, _ = await create_service(db, cipher, name="notify-save-svc")
    csrf = await _admin(client, db, redis_client)

    # 정상 저장
    r = await client.post(f"/admin/services/{svc.id}/notification-url",
                          data={"csrf_token": csrf,
                                "notification_url": "https://hook.example.com/notify"})
    assert r.status_code == 303
    await db.refresh(svc)
    assert svc.notification_url == "https://hook.example.com/notify"

    # 빈값 → NULL(알림 끔)
    r = await client.post(f"/admin/services/{svc.id}/notification-url",
                          data={"csrf_token": csrf, "notification_url": ""})
    assert r.status_code == 303
    await db.refresh(svc)
    assert svc.notification_url is None

    # 잘못된 형식(http 아님) → 거부, 값 변경 없음
    r = await client.post(f"/admin/services/{svc.id}/notification-url",
                          data={"csrf_token": csrf, "notification_url": "ftp://bad"})
    assert r.status_code == 303 and "error=" in r.headers["location"]
    await db.refresh(svc)
    assert svc.notification_url is None


async def test_notification_test_button_sends(client, db, redis_client, cipher, notifier):
    """'테스트 알림 전송' 라우트 — URL 등록 시 send_test 호출(RecordingServiceNotifier 기록)."""
    svc, _, _ = await create_service(db, cipher, name="notify-test-svc")
    svc.notification_url = "https://hook.example.com/notify"
    await db.commit()
    csrf = await _admin(client, db, redis_client)
    r = await client.post(f"/admin/services/{svc.id}/notification-test",
                          data={"csrf_token": csrf})
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    # app 픽스처의 RecordingServiceNotifier에 테스트 알림이 기록된다
    assert any(m["EVENT"] == "notification.test" for m in notifier.sent)


async def test_notification_test_without_url_errors(client, db, redis_client, cipher):
    """알림 URL 미등록 서비스에 테스트 전송 → 실패 메시지(?error=)."""
    svc, _, _ = await create_service(db, cipher, name="notify-test-nourl")
    csrf = await _admin(client, db, redis_client)
    r = await client.post(f"/admin/services/{svc.id}/notification-test",
                          data={"csrf_token": csrf})
    assert r.status_code == 303 and "error=" in r.headers["location"]
