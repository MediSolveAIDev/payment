"""get_client_ip의 X-Forwarded-For 처리 단위 테스트 (감사 Phase 1 — 보안 M-5).

핵심 검증: trust_proxy=True일 때 XFF의 '오른쪽에서 trust_proxy_hops번째'를
취해야 한다 — 맨 왼쪽을 신뢰하면 클라이언트가 화이트리스트 IP를 위조해
IP 검사를 우회할 수 있다.

Request/Settings는 get_client_ip가 사용하는 속성만 가진 경량 스텁으로 대체한다.
"""
from types import SimpleNamespace

from app.api.deps import get_client_ip


def _request(xff: str | None, peer: str = "10.0.0.9"):
    """get_client_ip가 접근하는 속성(headers.get / client.host)만 흉내 낸 스텁."""
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


def _settings(trust_proxy: bool, hops: int = 1):
    return SimpleNamespace(trust_proxy=trust_proxy, trust_proxy_hops=hops)


def test_trust_proxy_off_ignores_xff():
    """trust_proxy=False면 XFF를 완전히 무시하고 소켓 피어 IP를 쓴다."""
    req = _request("1.2.3.4", peer="10.0.0.9")
    assert get_client_ip(req, _settings(False)) == "10.0.0.9"


def test_single_hop_takes_rightmost():
    """프록시 1단: 프록시가 append한 오른쪽 끝 값이 실제 클라이언트 IP다."""
    req = _request("203.0.113.5")
    assert get_client_ip(req, _settings(True, hops=1)) == "203.0.113.5"


def test_single_hop_spoof_attempt_is_ignored():
    """공격자가 화이트리스트 IP를 왼쪽에 끼워 넣어도(스푸핑) 오른쪽 값을 쓴다.

    프록시는 자신이 본 피어 IP를 항상 오른쪽에 append하므로
    'spoofed, real' 형태가 되고, 오른쪽의 real이 선택돼야 한다.
    """
    req = _request("198.51.100.77, 203.0.113.5")
    assert get_client_ip(req, _settings(True, hops=1)) == "203.0.113.5"


def test_two_hops_takes_second_from_right():
    """프록시 2단(LB→nginx 등): 오른쪽에서 2번째가 실제 클라이언트 IP다."""
    req = _request("198.51.100.77, 203.0.113.5, 10.1.1.1")
    assert get_client_ip(req, _settings(True, hops=2)) == "203.0.113.5"


def test_fewer_entries_than_hops_falls_back_to_peer():
    """XFF 항목이 hop 수보다 적으면(프록시 미경유 직접 요청 의심) 헤더를 무시한다."""
    req = _request("198.51.100.77", peer="10.0.0.9")
    assert get_client_ip(req, _settings(True, hops=2)) == "10.0.0.9"


def test_missing_xff_falls_back_to_peer():
    """trust_proxy=True여도 XFF가 없으면 소켓 피어 IP로 폴백한다."""
    req = _request(None, peer="10.0.0.9")
    assert get_client_ip(req, _settings(True, hops=1)) == "10.0.0.9"


def test_whitespace_and_empty_entries_are_normalized():
    """공백·빈 항목이 섞여도 정상적으로 파싱한다."""
    req = _request("  198.51.100.77 , , 203.0.113.5  ")
    assert get_client_ip(req, _settings(True, hops=1)) == "203.0.113.5"
