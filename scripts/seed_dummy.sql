-- Dummy data for endpoint testing.
--
-- All ids are in the 9000+ range so nothing collides with the migrated legacy
-- data (highest real ids: clients 17, plans 42, days 155, exercises 927).
--
--   load:     docker exec -i grind-postgres psql -U postgres -d grind_db < scripts/seed_dummy.sql
--   teardown: docker exec -i grind-postgres psql -U postgres -d grind_db < scripts/teardown_dummy.sql
--
-- NOTE: do not run scripts/reset_sequences.py while this is loaded — it sets each
-- sequence to MAX(id), which would jump them into the 9000s. Tear down first.
--
-- access_token values are NOT arbitrary. They are what build_access_token()
-- produces for (name, id), so the portal resolves them:
--   'Alpha Tester' + 9001 -> GR_ALP_009001
--   'Bravo Singh'  + 9002 -> GR_BRA_009002
--   'Cy'           + 9003 -> GR_CYX_009003   ("Cy" pads RIGHT to "CyX")

BEGIN;

-- ── clients ────────────────────────────────────────────────────────
INSERT INTO clients (id, name, email, phone, goal, status, access_token, created_at) VALUES
  (9001, 'Alpha Tester', 'alpha@example.com', '9990000001', 'Muscle Gain',  'active',   'GR_ALP_009001', '2026-01-10 09:00:00+00'),
  (9002, 'Bravo Singh',  'bravo@example.com', '9990000002', 'Fat Loss',     'active',   'GR_BRA_009002', '2026-02-14 09:00:00+00'),
  (9003, 'Cy',           'cy@example.com',    '9990000003', 'Endurance',    'inactive', 'GR_CYX_009003', '2026-03-20 09:00:00+00');

-- ── workout_plans ──────────────────────────────────────────────────
-- 9001 is the superseded plan, 9002 the live one. GET /workout for client 9001
-- must return 9002 (newest row where is_active is true).
INSERT INTO workout_plans (id, client_id, plan_name, is_active, version_no, workout_json, created_at) VALUES
  (9001, 9001, 'Foundation Phase (superseded)', false, 1, '{"plan_name":"Foundation Phase","days":[]}', '2026-01-10 10:00:00+00'),
  (9002, 9001, 'Strength Block',                true,  2, '{"plan_name":"Strength Block","days":[{"day_name":"Push"},{"day_name":"Pull"}]}', '2026-04-01 10:00:00+00'),
  (9003, 9002, 'Fat Loss Kickstart',            true,  1, '{"plan_name":"Fat Loss Kickstart","days":[{"day_name":"Full Body"}]}', '2026-02-14 10:00:00+00');
-- client 9003 deliberately has NO plan -> exercises the data:null branch.

-- ── workout_days ───────────────────────────────────────────────────
INSERT INTO workout_days (id, plan_id, day_number, day_name) VALUES
  (9001, 9002, 1, 'Day 1 - Push'),
  (9002, 9002, 2, 'Day 2 - Pull'),
  (9003, 9003, 1, 'Day 1 - Full Body');

-- ── workout_exercises ──────────────────────────────────────────────
-- 9005 has a NULL sort_order on purpose: the query orders nulls FIRST, so it
-- should come back ahead of 9001/9002 on day 9001.
INSERT INTO workout_exercises (id, day_id, exercise_name, sets_count, reps, youtube_url, notes, sort_order) VALUES
  (9005, 9001, 'Warm-up Mobility', 1, '5 min',  NULL,                                          'no sort_order set',   NULL),
  (9001, 9001, 'Barbell Bench Press', 4, '8-10', 'https://youtube.com/watch?v=dummy1',         'Control the descent',    1),
  (9002, 9001, 'Overhead Press',      3, '10',   'https://youtube.com/watch?v=dummy2',         NULL,                     2),
  (9003, 9002, 'Deadlift',            4, '5',    'https://youtube.com/watch?v=dummy3',         'Neutral spine',          1),
  (9004, 9003, 'Burpees',             3, '15',   NULL,                                          NULL,                    1);

-- ── workout_logs ───────────────────────────────────────────────────
-- Two DISTINCT exercise ids completed for alpha -> the portal progress count
-- (COUNT(DISTINCT exercise_id) WHERE completed) reports 2.
INSERT INTO workout_logs (id, user_email, month_no, week_no, day_id, exercise_id, set_no, completed, created_at) VALUES
  (9001, 'alpha@example.com', 1, 1, 9001, 9001, 1, true,  '2026-04-02 07:30:00+00'),
  (9002, 'alpha@example.com', 1, 1, 9001, 9002, 1, true,  '2026-04-02 07:45:00+00'),
  (9003, 'alpha@example.com', 1, 1, 9002, 9003, 1, false, '2026-04-03 07:30:00+00');

-- ── client_progress ────────────────────────────────────────────────
-- Two rows so start/current deltas are non-zero: 82.0 -> 78.5 kg, 36 -> 34 in.
INSERT INTO client_progress (id, client_id, weight, waist, chest, arms, thighs, notes, created_at) VALUES
  (9001, 9001, 82.00, 36.00, 40.00, 14.00, 22.00, 'baseline',   '2026-01-10 08:00:00+00'),
  (9002, 9001, 78.50, 34.00, 41.00, 14.50, 22.50, 'week 12',    '2026-04-05 08:00:00+00');

-- ── diet_plans ─────────────────────────────────────────────────────
INSERT INTO diet_plans (id, client_id, plan_name, diet_json, is_active, created_at) VALUES
  (9001, 9001, 'High Protein 2200kcal',
   '{"meals":[{"name":"Breakfast","items":["4 egg whites","Oats 60g"]},{"name":"Lunch","items":["Chicken 200g","Rice 150g"]}]}',
   true, '2026-04-01 11:00:00+00');

-- ── affiliate_codes ────────────────────────────────────────────────
-- DUMMYEXP is active but EXPIRED. It demonstrates the deliberate legacy split:
-- POST /affiliate/validate accepts it, POST /payments/order refuses the discount.
INSERT INTO affiliate_codes (id, code, affiliate_name, affiliate_email, discount_percent, commission_percent, total_sales, total_revenue, status, expiry_date, created_at) VALUES
  (9001, 'DUMMY20',  'Dummy Partner',  'partner@example.com', 20, 10.00, 1, 1520.00, 'active',   '2027-12-31', '2026-01-01 00:00:00+00'),
  (9002, 'DUMMYEXP', 'Expired Partner','expired@example.com', 50,  5.00, 0,    0.00, 'active',   '2025-01-01', '2024-06-01 00:00:00+00'),
  (9003, 'DUMMYOFF', 'Disabled Partner','off@example.com',    30,  5.00, 0,    0.00, 'inactive', '2027-12-31', '2026-01-01 00:00:00+00');

-- ── enrollments ────────────────────────────────────────────────────
-- coupon_code + payment_status 'Paid' is what the affiliate dashboard joins on.
INSERT INTO enrollments (id, name, email, phone, plan_name, original_price, discount_percent, coupon_code, final_price, razorpay_payment_id, razorpay_order_id, payment_status, created_at) VALUES
  (9001, 'Alpha Tester', 'alpha@example.com', '9990000001', '3 MONTH KICKSTART', 1900.00, 20, 'DUMMY20', 1520.00, 'pay_DUMMY0001', 'order_DUMMY0001', 'Paid',    '2026-01-10 09:30:00+00'),
  (9002, 'Bravo Singh',  'bravo@example.com', '9990000002', '6 MONTH TRANSFORMATION', 3400.00, 0, '',   3400.00, 'pay_DUMMY0002', 'order_DUMMY0002', 'Pending', '2026-02-14 09:30:00+00');

-- ── affiliate_conversions ──────────────────────────────────────────
-- No endpoint writes this table (kept for schema fidelity); seeded so a SELECT
-- against it is not empty.
INSERT INTO affiliate_conversions (id, affiliate_code, plan_name, amount_paid, customer_name, customer_email, created_at) VALUES
  (9001, 'DUMMY20', '3 MONTH KICKSTART', 1520.00, 'Alpha Tester', 'alpha@example.com', '2026-01-10 09:31:00+00');

-- ── workout_progress ───────────────────────────────────────────────
-- Also unwritten by any endpoint; seeded for completeness.
INSERT INTO workout_progress (id, client_id, exercise_id, set_number, completed, completed_at) VALUES
  (9001, 9001, 9001, 1, true, '2026-04-02 07:30:00+00');

COMMIT;
