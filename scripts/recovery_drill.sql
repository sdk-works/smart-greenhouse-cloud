-- 恢复验收演练（只在测试库 greenhouse_nightly_test 执行，勿对生产跑）
--
-- 为什么要有这份演练：设备明天才可能上电，而 `verify_recovery.py` 的 SQL
-- (`JOIN devices ON d.id = t.device_id`) 从来没在真库上跑通"恢复后"这一路
-- —— 昨夜与 10-24 夜间对生产只跑出了"未恢复"这一支，单元测试又是纯内存数据。
-- 若这条 SQL 有 bug，会在设备上电那一刻才暴露，而那时最需要的是可信结论。
--
-- 三个场景（设备 CRV-SIM-A / CRV-SIM-B / CRV-SIM-C）：
--   A 正常恢复：boot 31 → 32，seq 从 1 起，60s 节律，断电窗口内无数据
--   B 同 boot 续传：boot 仍是 31、seq 从 923 续 —— 应判"不符合期望"
--   C 窗口内冒出数据：断电期间来了 3 条 —— 必须被点出来，不能默默忽略
--
-- 用法：
--   sudo -n -u postgres psql -d greenhouse_nightly_test -P pager=off -f recovery_drill.sql
--   /opt/greenhouse/venv/bin/python scripts/verify_recovery.py \
--       --db-url "postgresql://greenhouse:...@localhost:5432/greenhouse_nightly_test" \
--       --device CRV-SIM-A --period 60   （B / C 同理）

BEGIN;

-- 清掉上一次演练的痕迹（幂等）
DELETE FROM telemetry  WHERE device_id IN (SELECT id FROM devices WHERE device_id LIKE 'CRV-SIM-%');
DELETE FROM devices    WHERE device_id LIKE 'CRV-SIM-%';

INSERT INTO devices (id, device_id, name, device_type, status, created_at, updated_at)
VALUES ('crv-sim-a', 'CRV-SIM-A', '演练-正常恢复',   'SENSOR', 'ONLINE', now(), now()),
       ('crv-sim-b', 'CRV-SIM-B', '演练-同boot续传', 'SENSOR', 'ONLINE', now(), now()),
       ('crv-sim-c', 'CRV-SIM-C', '演练-窗口内冒数据','SENSOR', 'ONLINE', now(), now());

-- ── 场景 A：断电前 5 条（boot 31, seq 918~922）── 3 小时静默 ── 恢复后 20 条（boot 32, seq 1~20）
INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'a-pre-' || s, 'crv-sim-a',
       now() - interval '3 hours' - ((922 - s) * interval '60 seconds'),
       now() - interval '3 hours' - ((922 - s) * interval '60 seconds'),
       'CRV-SIM-A/31/' || s, 25.0
FROM generate_series(918, 922) AS s;

INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'a-post-' || s, 'crv-sim-a',
       now() - ((20 - s) * interval '60 seconds'),
       now() - ((20 - s) * interval '60 seconds'),
       'CRV-SIM-A/32/' || s, 26.0
FROM generate_series(1, 20) AS s;

-- ── 场景 B：同样的断点，但恢复后 boot 仍是 31、seq 从 923 续（MCU 没重启）
INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'b-pre-' || s, 'crv-sim-b',
       now() - interval '3 hours' - ((922 - s) * interval '60 seconds'),
       now() - interval '3 hours' - ((922 - s) * interval '60 seconds'),
       'CRV-SIM-B/31/' || s, 25.0
FROM generate_series(918, 922) AS s;

INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'b-post-' || s, 'crv-sim-b',
       now() - ((10 - s) * interval '60 seconds'),
       now() - ((10 - s) * interval '60 seconds'),
       'CRV-SIM-B/31/' || (922 + s), 26.0
FROM generate_series(1, 10) AS s;

-- ── 场景 C：正常恢复，但断电窗口里冒出 3 条（缓存补传 / 有人补数）
INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'c-pre-' || s, 'crv-sim-c',
       now() - interval '3 hours' - ((922 - s) * interval '60 seconds'),
       now() - interval '3 hours' - ((922 - s) * interval '60 seconds'),
       'CRV-SIM-C/31/' || s, 25.0
FROM generate_series(918, 922) AS s;

INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'c-post-' || s, 'crv-sim-c',
       now() - ((10 - s) * interval '60 seconds'),
       now() - ((10 - s) * interval '60 seconds'),
       'CRV-SIM-C/32/' || s, 26.0
FROM generate_series(1, 10) AS s;

-- 窗口里的 3 条：落在断电前最后一条之后、恢复首条之前
INSERT INTO telemetry (id, device_id, timestamp, created_at, msg_id, temperature)
SELECT 'c-inwin-' || s, 'crv-sim-c',
       now() - interval '90 minutes' - ((3 - s) * interval '60 seconds'),
       now() - interval '90 minutes' - ((3 - s) * interval '60 seconds'),
       'CRV-SIM-C/31/' || (930 + s), 24.0
FROM generate_series(1, 3) AS s;

COMMIT;

\echo '=== 演练数据已就绪 ==='
SELECT d.device_id,
       count(*) AS rows,
       min(t.created_at) AS first_at,
       max(t.created_at) AS last_at
FROM telemetry t JOIN devices d ON d.id = t.device_id
WHERE d.device_id LIKE 'CRV-SIM-%'
GROUP BY d.device_id ORDER BY d.device_id;
