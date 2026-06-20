-- DB 데이터 초기화 — SYSTEM_ADMIN(마스터 계정)과 global_settings(전역 설정)만 유지.
-- 모든 업무 데이터(서비스·요금제·구독·결제·카드·웹훅·감사로그·서비스담당자 등)를 삭제한다.
-- FK 의존 순서를 지켜 자식 → 부모 순으로 지우며, 단일 트랜잭션으로 원자적 실행한다.
-- 사용: docker compose exec -T postgres psql -U payment -d payment -f - < scripts/reset_db_keep_admin.sql
--       (또는 psql ... -f scripts/reset_db_keep_admin.sql)
BEGIN;

-- 1) 결제·구독 관련(자식부터)
DELETE FROM payments;          -- subscriptions/services 참조
DELETE FROM subscriptions;     -- plans/services/cards 참조
DELETE FROM cards;             -- services 참조 (빌링키 보관함)
DELETE FROM plans;             -- services 참조

-- 2) 운영 기록
DELETE FROM webhook_events;
DELETE FROM audit_logs;        -- 감사 이력도 초기화(프레시 스타트)

-- 3) 계정/연결 — 마스터(SYSTEM_ADMIN)만 남긴다
DELETE FROM password_setup_tokens;                       -- 모든 초기설정 토큰 제거
DELETE FROM user_services;                               -- 담당자↔서비스 연결 제거
DELETE FROM users WHERE role <> 'SYSTEM_ADMIN';          -- 서비스 담당자 등 비-마스터 계정 제거

-- 4) 서비스(마지막 — 위에서 참조 행을 모두 지운 뒤)
DELETE FROM services;

-- global_settings(전역 설정: 재시도 정책·어드민 IP·킬스위치)는 유지한다.
-- (관리자 운영 설정이라 초기화 대상에서 제외)

COMMIT;
