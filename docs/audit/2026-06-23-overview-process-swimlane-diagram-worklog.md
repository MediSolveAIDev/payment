# 개요 0-2 전체 프로세스 역할 스윔레인 그림 워크로그

- 날짜: 2026-06-23
- 작업자: seungjinhan (oasis@medisolveai.com)

## 목적

user_manual `00-overview.md` 0-2절 "전체 프로세스 그림 — 서비스 입장에서"의 1~9 단계를, 기존 단계 카드(`.flow`)에 더해 **역할 스윔레인 다이어그램**(인라인 SVG)으로도 표현. "어느 역할이 어느 단계를 하는지"를 한눈에.

## 변경

- `docs/user_manual/00-overview.md`: 0-2절 "함께 보기" 뒤, "(개발자용) 연동 시퀀스" 앞에 역할 스윔레인 SVG + 리드 문장 추가.
  - 4개 역할 레인: 시스템 관리자(①서비스등록 ②API키발급 ③토스 시크릿 키 등록) · 서비스 담당자(④로그인 ⑤요금제) · 외부 서비스(⑥카드 ⑦구독) · 결제 서버·스케줄러(⑧자동연장 ⑨정산·알림).
  - 구간 밴드: 준비(1~5) ‖ 일상(6~9). 파란 화살표 ①→⑨ 단일 경로.
  - 기존 `.seqwrap` 클래스(lane/band/msg/lbl 등) 재사용, 노드(번호 원·라벨)는 인라인 속성. **manual.css 변경 없음.** 마커 id `fp`(기존 SVG `apr`/`agr`와 비중복).
  - `role="img"` + `aria-label`, 모바일 가로 스크롤.
- 기존 단계 카드·개발자 연동 시퀀스 SVG는 그대로 유지(보완).

## 검증

- `uv run --with markdown python docs/user_manual/build.py` → 19개 문서 재빌드, `00-overview.html`에 SVG(aria-label·marker·노드 라벨) 보존 확인.
- docker dev 이미지 재빌드(`docker compose -f docker-compose.dev.yml up -d --build`) → `GET /user-manual/00-overview.html` 라이브에 스윔레인 SVG 반영 확인(health 200).

## 참고

- 설계: docs/superpowers/specs/2026-06-23-overview-process-swimlane-diagram-design.md
- docker dev 컨테이너는 docs를 이미지에 굽기 때문에 문서 변경 후 이미지 재빌드 필요(또는 dev compose에 docs 볼륨 마운트 — 별도 제안).
