-- Removes everything scripts/seed_dummy.sql inserted.
--
--   docker exec -i grind-postgres psql -U postgres -d grind_db < scripts/teardown_dummy.sql
--
-- Every seeded row uses an id >= 9000, so this cannot touch migrated legacy data.
-- The workout_logs delete also matches on email, to catch rows created by hitting
-- POST /workout/complete against the dummy clients during testing.

BEGIN;

DELETE FROM workout_progress      WHERE id >= 9000;
DELETE FROM affiliate_conversions WHERE id >= 9000;
DELETE FROM enrollments           WHERE id >= 9000 OR email IN ('alpha@example.com', 'bravo@example.com', 'cy@example.com');
DELETE FROM affiliate_codes       WHERE id >= 9000;
DELETE FROM diet_plans            WHERE id >= 9000 OR client_id >= 9000;
DELETE FROM client_progress       WHERE id >= 9000 OR client_id >= 9000;
DELETE FROM workout_logs          WHERE id >= 9000 OR user_email IN ('alpha@example.com', 'bravo@example.com', 'cy@example.com');
DELETE FROM workout_exercises     WHERE id >= 9000 OR day_id >= 9000;
DELETE FROM workout_days          WHERE id >= 9000 OR plan_id >= 9000;
DELETE FROM workout_plans         WHERE id >= 9000 OR client_id >= 9000;
DELETE FROM clients               WHERE id >= 9000;

COMMIT;
