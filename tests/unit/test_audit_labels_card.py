"""감사로그 카드 이벤트 라벨/상세 — 한글 라벨과 detail 요약 검증(순수 함수)."""
from app.admin.audit_labels import action_label, detail_summary, target_label


def test_card_action_labels_are_korean():
    assert action_label("card.register") == "카드 등록"
    assert action_label("card.replace") == "카드 교체"
    assert action_label("card.delete") == "카드 삭제"
    assert action_label("card.activate") == "카드 활성화"
    assert action_label("card.deactivate") == "카드 비활성화"


def test_card_target_label():
    assert target_label("card", "user-1") == "카드 · user-1"


def test_card_detail_summary_shows_user_and_masked_number():
    """카드 detail은 사용자·카드번호·발급사를 보여주고, 스코프용 service_id는 표시하지 않는다."""
    summary = detail_summary({
        "external_user_id": "u-1@e.com",
        "service_id": "0c4a-uuid-scope-only",   # 표시되면 안 됨(원시 UUID)
        "card_number": "1234-****-****-5678",
        "issuer": "61",
        "is_active": False,
    })
    assert "사용자 u-1" in summary
    assert "카드번호 1234-****-****-5678" in summary
    assert "발급사 61" in summary
    assert "0c4a-uuid-scope-only" not in summary  # service_id 원시값 미표시
