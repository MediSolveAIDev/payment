"""계정 추가 폼이 data-loading 속성을 렌더하는지 검증(제출 로딩 UX opt-in)."""
from tests.factories import create_user
from tests.helpers import admin_login

async def test_account_new_form_has_loading_attr(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin/users/new")).text
    assert "data-loading" in html
    assert 'data-loading-text="설정 메일 발송 중…"' in html
