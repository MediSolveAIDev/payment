-- docker-entrypoint-initdb.d는 빈 데이터 볼륨에서만 실행되지만,
-- 수동 재실행에도 안전하도록 멱등 처리.
SELECT 'CREATE DATABASE payment_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'payment_test')\gexec
