-- ============================================================
-- CLIENTS
-- ============================================================

INSERT INTO clients (
    id,
    name,
    email,
    phone,
    goal,
    status,
    password_hash,
    last_login,
    created_at,
    access_token
) VALUES
(
    1,
    'Arup',
    'arup.theorem@gmail.com',
    '',
    'Upper body transformation',
    'active',
    NULL,
    NULL,
    '2026-06-14 18:11:46',
    'ARUP123XYZ'
),
(
    2,
    'Test Client',
    'test@test.com',
    '',
    'Broad Chest',
    'active',
    NULL,
    NULL,
    '2026-06-17 17:15:24',
    'TESTCLIENT123'
),
(
    3,
    'Venu Madhav',
    'madhavan.vmj@gmail.com',
    '',
    'Fat Loss',
    'active',
    NULL,
    NULL,
    '2026-06-17 17:56:13',
    'GR_Venu-123'
),
(
    4,
    'Nigama',
    'rahul@test.com',
    '',
    'Broad Shoulders',
    'active',
    NULL,
    NULL,
    '2026-06-18 07:55:20',
    'GR_RAH_000004'
),
(
    7,
    'Version Test',
    'version@test.com',
    '',
    'Weight Loss',
    'active',
    NULL,
    NULL,
    '2026-06-18 09:08:03',
    'GR_VER_000007'
),
(
    8,
    'Nigama Medhi',
    'Nigama@test.com',
    '',
    'General Fitness',
    'active',
    NULL,
    NULL,
    '2026-06-18 10:57:28',
    'GR_NIG_000008'
),
(
    9,
    'Krishna',
    'krishna@test.com',
    '',
    'Strength gain',
    'active',
    NULL,
    NULL,
    '2026-06-18 11:19:33',
    'GR_KRI_000009'
),
(
    10,
    'Hriti Dhar',
    'askhapok@gmail.com',
    '',
    'Fat loss',
    'active',
    NULL,
    NULL,
    '2026-06-22 16:38:17',
    'GR_HRI_000010'
),
(
    11,
    'Ankita Mohanty',
    'ankitamohanty205@gmail.com',
    '',
    'Fat Loss, Strength',
    'active',
    NULL,
    NULL,
    '2026-06-24 10:23:16',
    'GR_ANK_000011'
),
(
    12,
    'Dibyalok',
    'the.divyalok@gmail.com',
    '',
    'Fat Loss, Muscle Gain, Strength, General Fitness',
    'active',
    NULL,
    NULL,
    '2026-07-12 04:30:54',
    'GR_DIB_000012'
),
(
    13,
    'Ayusha Nayak',
    'massnayak2000@gmail.com',
    '',
    'Fatloss Plan',
    'active',
    NULL,
    NULL,
    '2026-07-20 17:30:48',
    'GR_AYU_000013'
),
(
    14,
    'Arup Mohanty',
    'arup.mohanty29j@gmail.com',
    '',
    'Fat loss',
    'active',
    NULL,
    NULL,
    '2026-07-23 07:29:23',
    'GR_ARU_000014'
),
(
    15,
    'Tanuj Mohanty',
    'tanuj123mohanty@gmail.com',
    '',
    'Fatloss and strength',
    'active',
    NULL,
    NULL,
    '2026-08-04 16:01:06',
    'GR_TAN_000015'
),
(
    16,
    'Ankit Mohanty',
    'ankit1234mohanty@gmail.com',
    '',
    'Fat loss',
    'active',
    NULL,
    NULL,
    '2026-08-08 11:47:42',
    'GR_ANK_000016'
),
(
    17,
    'Rameswar Bhagat',
    'rameswarbhagat199@gmail.com',
    '',
    'Muscle Gain, Strength',
    'active',
    NULL,
    NULL,
    '2026-08-08 12:23:50',
    'GR_RAM_000017'
)
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('clients', 'id'),
    COALESCE((SELECT MAX(id) FROM clients), 1),
    true
);


-- ============================================================
-- CLIENT PROGRESS
-- ============================================================

INSERT INTO client_progress (
    id,
    client_id,
    weight,
    waist,
    chest,
    arms,
    thighs,
    notes,
    created_at
) VALUES
(
    1,
    1,
    75.00,
    34.00,
    41.50,
    17.00,
    22.00,
    '',
    '2026-06-15 11:48:02'
),
(
    2,
    1,
    73.00,
    32.00,
    41.00,
    16.80,
    21.50,
    'Test Progress',
    '2026-06-16 08:34:04'
)
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('client_progress', 'id'),
    COALESCE((SELECT MAX(id) FROM client_progress), 1),
    true
);


-- ============================================================
-- AFFILIATE CODES
-- ============================================================

INSERT INTO affiliate_codes (
    id,
    code,
    affiliate_name,
    affiliate_email,
    discount_percent,
    commission_percent,
    total_sales,
    total_revenue,
    status,
    created_at,
    expiry_date
) VALUES
(
    1,
    'GR_ARU_10',
    'Arup',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-20 08:30:02',
    '2026-06-23'
),
(
    2,
    'GR_ANU_10',
    'Anup',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-22 15:59:15',
    '2026-12-31'
),
(
    3,
    'GR_HRI_10',
    'Hriti',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-22 16:00:07',
    '2026-12-31'
),
(
    4,
    'GR_RAJ_10',
    'Raj',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-22 16:01:04',
    '2027-12-31'
),
(
    5,
    'GR_NIR_10',
    'Niraj',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-23 05:32:11',
    '2026-12-31'
),
(
    6,
    'GR_NIS_10',
    'Nisha',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-23 05:32:26',
    '2026-12-31'
),
(
    7,
    'GR_ANK_10',
    'Ankita Mohanty',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-24 11:02:04',
    '2026-12-31'
),
(
    8,
    'GR_FIT_30',
    'FITRIG',
    NULL,
    30,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-30 16:58:50',
    '2026-12-31'
),
(
    9,
    'GR_PSS_30',
    'PSS',
    NULL,
    30,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-30 17:18:54',
    '2026-12-31'
),
(
    10,
    'GR_VK_30',
    'VK',
    NULL,
    30,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-30 17:19:39',
    '2026-12-31'
),
(
    11,
    'GR_SIK_10',
    'SIKHA',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-06-30 17:21:09',
    '2026-12-31'
),
(
    12,
    'GR_INT_30',
    'Intro_offer',
    NULL,
    30,
    0.00,
    0,
    0.00,
    'active',
    '2026-07-01 12:09:52',
    '2026-12-31'
),
(
    13,
    'GR_TAN_10',
    'Tanuj',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-08-05 09:50:27',
    '2027-02-05'
),
(
    14,
    'GR_AYU_10',
    'Ayusha',
    NULL,
    10,
    0.00,
    0,
    0.00,
    'active',
    '2026-08-05 09:50:27',
    '2027-02-05'
),
(
    15,
    'GR_INDIA_30',
    'Independence',
    NULL,
    30,
    0.00,
    0,
    0.00,
    'active',
    '2026-08-14 17:47:29',
    '2026-08-20'
)
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('affiliate_codes', 'id'),
    COALESCE((SELECT MAX(id) FROM affiliate_codes), 1),
    true
);


-- ============================================================
-- ENROLLMENTS
-- ============================================================

INSERT INTO enrollments (
    id,
    name,
    email,
    phone,
    plan_name,
    original_price,
    discount_percent,
    coupon_code,
    final_price,
    razorpay_payment_id,
    razorpay_order_id,
    payment_status,
    created_at
) VALUES
(
    1,
    'Test',
    'test@test.com',
    '1234567890',
    '3 MONTH KICKSTART',
    10.00,
    0,
    '',
    10.00,
    'pay_T4MbpGZm2Wyxjt',
    'order_T4MbN6Tgw6oIvc',
    'Paid',
    '2026-06-21 17:28:32'
)
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('enrollments', 'id'),
    COALESCE((SELECT MAX(id) FROM enrollments), 1),
    true
);


-- ============================================================
-- WORKOUT LOGS
-- ============================================================

INSERT INTO workout_logs (
    id,
    user_email,
    month_no,
    week_no,
    day_id,
    exercise_id,
    set_no,
    completed,
    created_at
) VALUES
(
    1,
    'arup.theorem@gmail.com',
    1,
    1,
    3,
    4,
    1,
    true,
    '2026-06-15 11:25:44'
)
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('workout_logs', 'id'),
    COALESCE((SELECT MAX(id) FROM workout_logs), 1),
    true
);


-- ============================================================
-- AFFILIATE CONVERSIONS
-- No rows to insert.
--
-- WORKOUT PROGRESS
-- No rows to insert.
-- ============================================================