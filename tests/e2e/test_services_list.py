"""GET /api/v1/services 무인증 엔드포인트 e2e 테스트.

인증 헤더 없이도 서비스 목록을 조회할 수 있고, 민감정보(api_key/hmac_secret 등)가
응답에 포함되지 않음을 검증한다.
"""
from tests.factories import create_service


async def test_services_list_no_auth_no_secrets(client, db, cipher):
    """인증 없이 서비스 목록 조회 + 응답에 민감정보 미포함 검증."""
    # 테스트용 서비스 생성
    svc, _, _ = await create_service(db, cipher, name="서비스목록테스트")

    # 인증 헤더 없이 호출
    resp = await client.get("/api/v1/services")

    assert resp.status_code == 200
    body = resp.json()

    # 생성한 서비스 이름이 목록에 포함돼야 한다
    names = [s["name"] for s in body["services"]]
    assert "서비스목록테스트" in names

    # 응답 필드가 정확히 {id, name, status}만이어야 한다(키/시크릿/해시 미포함)
    one = next(s for s in body["services"] if s["name"] == "서비스목록테스트")
    assert set(one.keys()) == {"id", "name", "status"}

    # 응답 본문에 민감정보 키워드가 없어야 한다
    text = resp.text.lower()
    assert "secret" not in text
    assert "api_key" not in text
    assert "hash" not in text


async def test_services_list_sorted_by_name(client, db, cipher):
    """서비스 목록이 이름 오름차순으로 정렬되는지 검증."""
    # 이름 순서가 역순인 서비스 두 개를 생성
    await create_service(db, cipher, name="가나다라마")
    await create_service(db, cipher, name="AAAA서비스")

    resp = await client.get("/api/v1/services")
    assert resp.status_code == 200

    names = [s["name"] for s in resp.json()["services"]]
    # 정렬 검증: 이름 오름차순이어야 한다
    assert names == sorted(names)


async def test_services_list_returns_id_as_string(client, db, cipher):
    """id 필드가 UUID 문자열로 반환되는지 검증."""
    svc, _, _ = await create_service(db, cipher, name="UUID문자열테스트")

    resp = await client.get("/api/v1/services")
    assert resp.status_code == 200

    one = next(s for s in resp.json()["services"] if s["name"] == "UUID문자열테스트")
    # id는 문자열(UUID 형식)이어야 한다
    assert isinstance(one["id"], str)
    assert str(svc.id) == one["id"]
