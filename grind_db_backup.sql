--
-- PostgreSQL database dump
--

\restrict f9ZLaHP98e0Q60W9bJbsLqAtgZO44pPZQDeeCLIQh0Nh1iRbVZl87azojKO5fwf

-- Dumped from database version 17.11
-- Dumped by pg_dump version 17.11

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: affiliate_codes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.affiliate_codes (
    id integer NOT NULL,
    code character varying(50),
    affiliate_name character varying(100),
    affiliate_email character varying(150),
    discount_percent integer DEFAULT 10,
    commission_percent numeric(5,2) DEFAULT 0.00,
    total_sales integer DEFAULT 0,
    total_revenue numeric(10,2) DEFAULT 0.00,
    status character varying(20) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expiry_date date
);


ALTER TABLE public.affiliate_codes OWNER TO postgres;

--
-- Name: affiliate_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.affiliate_codes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.affiliate_codes_id_seq OWNER TO postgres;

--
-- Name: affiliate_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.affiliate_codes_id_seq OWNED BY public.affiliate_codes.id;


--
-- Name: affiliate_conversions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.affiliate_conversions (
    id integer NOT NULL,
    affiliate_code character varying(50),
    plan_name character varying(100),
    amount_paid numeric(10,2),
    customer_name character varying(255),
    customer_email character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.affiliate_conversions OWNER TO postgres;

--
-- Name: affiliate_conversions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.affiliate_conversions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.affiliate_conversions_id_seq OWNER TO postgres;

--
-- Name: affiliate_conversions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.affiliate_conversions_id_seq OWNED BY public.affiliate_conversions.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: client_progress; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.client_progress (
    id integer NOT NULL,
    client_id integer NOT NULL,
    weight numeric(5,2),
    waist numeric(5,2),
    chest numeric(5,2),
    arms numeric(5,2),
    thighs numeric(5,2),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.client_progress OWNER TO postgres;

--
-- Name: client_progress_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.client_progress_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.client_progress_id_seq OWNER TO postgres;

--
-- Name: client_progress_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.client_progress_id_seq OWNED BY public.client_progress.id;


--
-- Name: clients; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.clients (
    id integer NOT NULL,
    name character varying(255),
    email character varying(255),
    phone character varying(50),
    goal character varying(255),
    status character varying(20) DEFAULT 'active'::character varying,
    password_hash character varying(255),
    last_login timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    access_token character varying(50)
);


ALTER TABLE public.clients OWNER TO postgres;

--
-- Name: clients_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.clients_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.clients_id_seq OWNER TO postgres;

--
-- Name: clients_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.clients_id_seq OWNED BY public.clients.id;


--
-- Name: diet_plans; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.diet_plans (
    id integer NOT NULL,
    client_id integer NOT NULL,
    plan_name character varying(255),
    diet_json text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.diet_plans OWNER TO postgres;

--
-- Name: diet_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.diet_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.diet_plans_id_seq OWNER TO postgres;

--
-- Name: diet_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.diet_plans_id_seq OWNED BY public.diet_plans.id;


--
-- Name: enrollments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.enrollments (
    id integer NOT NULL,
    name character varying(255),
    email character varying(255),
    phone character varying(50),
    plan_name character varying(100),
    original_price numeric(10,2),
    discount_percent integer DEFAULT 0,
    coupon_code character varying(50),
    final_price numeric(10,2),
    razorpay_payment_id character varying(255),
    razorpay_order_id character varying(255),
    payment_status character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.enrollments OWNER TO postgres;

--
-- Name: enrollments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.enrollments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.enrollments_id_seq OWNER TO postgres;

--
-- Name: enrollments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.enrollments_id_seq OWNED BY public.enrollments.id;


--
-- Name: workout_days; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.workout_days (
    id integer NOT NULL,
    plan_id integer,
    day_number integer,
    day_name character varying(255)
);


ALTER TABLE public.workout_days OWNER TO postgres;

--
-- Name: workout_days_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.workout_days_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.workout_days_id_seq OWNER TO postgres;

--
-- Name: workout_days_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.workout_days_id_seq OWNED BY public.workout_days.id;


--
-- Name: workout_exercises; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.workout_exercises (
    id integer NOT NULL,
    day_id integer,
    exercise_name character varying(255),
    sets_count integer,
    reps character varying(50),
    youtube_url text,
    notes text,
    sort_order integer
);


ALTER TABLE public.workout_exercises OWNER TO postgres;

--
-- Name: workout_exercises_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.workout_exercises_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.workout_exercises_id_seq OWNER TO postgres;

--
-- Name: workout_exercises_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.workout_exercises_id_seq OWNED BY public.workout_exercises.id;


--
-- Name: workout_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.workout_logs (
    id integer NOT NULL,
    user_email character varying(255) NOT NULL,
    month_no integer NOT NULL,
    week_no integer NOT NULL,
    day_id integer NOT NULL,
    exercise_id integer NOT NULL,
    set_no integer NOT NULL,
    completed boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.workout_logs OWNER TO postgres;

--
-- Name: workout_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.workout_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.workout_logs_id_seq OWNER TO postgres;

--
-- Name: workout_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.workout_logs_id_seq OWNED BY public.workout_logs.id;


--
-- Name: workout_plans; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.workout_plans (
    id integer NOT NULL,
    client_id integer,
    plan_name character varying(255),
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    workout_json text,
    version_no integer DEFAULT 1
);


ALTER TABLE public.workout_plans OWNER TO postgres;

--
-- Name: workout_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.workout_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.workout_plans_id_seq OWNER TO postgres;

--
-- Name: workout_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.workout_plans_id_seq OWNED BY public.workout_plans.id;


--
-- Name: workout_progress; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.workout_progress (
    id integer NOT NULL,
    client_id integer,
    exercise_id integer,
    set_number integer,
    completed boolean,
    completed_at timestamp with time zone
);


ALTER TABLE public.workout_progress OWNER TO postgres;

--
-- Name: workout_progress_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.workout_progress_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.workout_progress_id_seq OWNER TO postgres;

--
-- Name: workout_progress_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.workout_progress_id_seq OWNED BY public.workout_progress.id;


--
-- Name: affiliate_codes id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.affiliate_codes ALTER COLUMN id SET DEFAULT nextval('public.affiliate_codes_id_seq'::regclass);


--
-- Name: affiliate_conversions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.affiliate_conversions ALTER COLUMN id SET DEFAULT nextval('public.affiliate_conversions_id_seq'::regclass);


--
-- Name: client_progress id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.client_progress ALTER COLUMN id SET DEFAULT nextval('public.client_progress_id_seq'::regclass);


--
-- Name: clients id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.clients ALTER COLUMN id SET DEFAULT nextval('public.clients_id_seq'::regclass);


--
-- Name: diet_plans id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.diet_plans ALTER COLUMN id SET DEFAULT nextval('public.diet_plans_id_seq'::regclass);


--
-- Name: enrollments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.enrollments ALTER COLUMN id SET DEFAULT nextval('public.enrollments_id_seq'::regclass);


--
-- Name: workout_days id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_days ALTER COLUMN id SET DEFAULT nextval('public.workout_days_id_seq'::regclass);


--
-- Name: workout_exercises id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_exercises ALTER COLUMN id SET DEFAULT nextval('public.workout_exercises_id_seq'::regclass);


--
-- Name: workout_logs id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_logs ALTER COLUMN id SET DEFAULT nextval('public.workout_logs_id_seq'::regclass);


--
-- Name: workout_plans id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_plans ALTER COLUMN id SET DEFAULT nextval('public.workout_plans_id_seq'::regclass);


--
-- Name: workout_progress id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_progress ALTER COLUMN id SET DEFAULT nextval('public.workout_progress_id_seq'::regclass);


--
-- Data for Name: affiliate_codes; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.affiliate_codes (id, code, affiliate_name, affiliate_email, discount_percent, commission_percent, total_sales, total_revenue, status, created_at, expiry_date) FROM stdin;
9001	DUMMY20	Dummy Partner	partner@example.com	20	10.00	1	1520.00	active	2026-01-01 00:00:00+00	2027-12-31
9002	DUMMYEXP	Expired Partner	expired@example.com	50	5.00	0	0.00	active	2024-06-01 00:00:00+00	2025-01-01
9003	DUMMYOFF	Disabled Partner	off@example.com	30	5.00	0	0.00	inactive	2026-01-01 00:00:00+00	2027-12-31
1	GR_ARU_10	Arup	\N	10	0.00	0	0.00	active	2026-06-20 08:30:02+00	2026-06-23
2	GR_ANU_10	Anup	\N	10	0.00	0	0.00	active	2026-06-22 15:59:15+00	2026-12-31
3	GR_HRI_10	Hriti	\N	10	0.00	0	0.00	active	2026-06-22 16:00:07+00	2026-12-31
4	GR_RAJ_10	Raj	\N	10	0.00	0	0.00	active	2026-06-22 16:01:04+00	2027-12-31
5	GR_NIR_10	Niraj	\N	10	0.00	0	0.00	active	2026-06-23 05:32:11+00	2026-12-31
6	GR_NIS_10	Nisha	\N	10	0.00	0	0.00	active	2026-06-23 05:32:26+00	2026-12-31
7	GR_ANK_10	Ankita Mohanty	\N	10	0.00	0	0.00	active	2026-06-24 11:02:04+00	2026-12-31
8	GR_FIT_30	FITRIG	\N	30	0.00	0	0.00	active	2026-06-30 16:58:50+00	2026-12-31
9	GR_PSS_30	PSS	\N	30	0.00	0	0.00	active	2026-06-30 17:18:54+00	2026-12-31
10	GR_VK_30	VK	\N	30	0.00	0	0.00	active	2026-06-30 17:19:39+00	2026-12-31
11	GR_SIK_10	SIKHA	\N	10	0.00	0	0.00	active	2026-06-30 17:21:09+00	2026-12-31
12	GR_INT_30	Intro_offer	\N	30	0.00	0	0.00	active	2026-07-01 12:09:52+00	2026-12-31
13	GR_TAN_10	Tanuj	\N	10	0.00	0	0.00	active	2026-08-05 09:50:27+00	2027-02-05
14	GR_AYU_10	Ayusha	\N	10	0.00	0	0.00	active	2026-08-05 09:50:27+00	2027-02-05
15	GR_INDIA_30	Independence	\N	30	0.00	0	0.00	active	2026-08-14 17:47:29+00	2026-08-20
\.


--
-- Data for Name: affiliate_conversions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.affiliate_conversions (id, affiliate_code, plan_name, amount_paid, customer_name, customer_email, created_at) FROM stdin;
9001	DUMMY20	3 MONTH KICKSTART	1520.00	Alpha Tester	alpha@example.com	2026-01-10 09:31:00+00
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alembic_version (version_num) FROM stdin;
2964177e83c3
\.


--
-- Data for Name: client_progress; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.client_progress (id, client_id, weight, waist, chest, arms, thighs, notes, created_at) FROM stdin;
9001	9001	82.00	36.00	40.00	14.00	22.00	baseline	2026-01-10 08:00:00+00
9002	9001	78.50	34.00	41.00	14.50	22.50	week 12	2026-04-05 08:00:00+00
1	1	75.00	34.00	41.50	17.00	22.00		2026-06-15 11:48:02+00
2	1	73.00	32.00	41.00	16.80	21.50	Test Progress	2026-06-16 08:34:04+00
\.


--
-- Data for Name: clients; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.clients (id, name, email, phone, goal, status, password_hash, last_login, created_at, access_token) FROM stdin;
9001	Alpha Tester	alpha@example.com	9990000001	Muscle Gain	active	\N	\N	2026-01-10 09:00:00+00	GR_ALP_009001
9002	Bravo Singh	bravo@example.com	9990000002	Fat Loss	active	\N	\N	2026-02-14 09:00:00+00	GR_BRA_009002
9003	Cy	cy@example.com	9990000003	Endurance	inactive	\N	\N	2026-03-20 09:00:00+00	GR_CYX_009003
1	Arup	arup.theorem@gmail.com		Upper body transformation	active	\N	\N	2026-06-14 12:41:46+00	ARUP123XYZ
2	Test Client	test@test.com		Broad Chest	active	\N	\N	2026-06-17 11:45:24+00	TESTCLIENT123
3	Venu Madhav	madhavan.vmj@gmail.com		Fat Loss	active	\N	\N	2026-06-17 12:26:13+00	GR_Venu-123
4	Nigama	rahul@test.com		Broad Shoulders	active	\N	\N	2026-06-18 02:25:20+00	GR_RAH_000004
7	Version Test	version@test.com		Weight Loss	active	\N	\N	2026-06-18 03:38:03+00	GR_VER_000007
8	Nigama Medhi	Nigama@test.com		General Fitness	active	\N	\N	2026-06-18 05:27:28+00	GR_NIG_000008
9	Krishna	krishna@test.com		Strength gain	active	\N	\N	2026-06-18 05:49:33+00	GR_KRI_000009
10	Hriti Dhar	askhapok@gmail.com		Fat loss	active	\N	\N	2026-06-22 11:08:17+00	GR_HRI_000010
11	Ankita Mohanty	ankitamohanty205@gmail.com		Fat Loss, Strength	active	\N	\N	2026-06-24 04:53:16+00	GR_ANK_000011
12	Dibyalok	the.divyalok@gmail.com		Fat Loss, Muscle Gain, Strength, General Fitness	active	\N	\N	2026-07-11 23:00:54+00	GR_DIB_000012
13	Ayusha Nayak	massnayak2000@gmail.com		Fatloss Plan	active	\N	\N	2026-07-20 12:00:48+00	GR_AYU_000013
14	Arup Mohanty	arup.mohanty29j@gmail.com		Fat loss	active	\N	\N	2026-07-23 01:59:23+00	GR_ARU_000014
15	Tanuj Mohanty	tanuj123mohanty@gmail.com		Fatloss and strength	active	\N	\N	2026-08-04 10:31:06+00	GR_TAN_000015
16	Ankit Mohanty	ankit1234mohanty@gmail.com		Fat loss	active	\N	\N	2026-08-08 06:17:42+00	GR_ANK_000016
17	Rameswar Bhagat	rameswarbhagat199@gmail.com		Muscle Gain, Strength	active	\N	\N	2026-08-08 06:53:50+00	GR_RAM_000017
\.


--
-- Data for Name: diet_plans; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.diet_plans (id, client_id, plan_name, diet_json, is_active, created_at) FROM stdin;
9001	9001	High Protein 2200kcal	{"meals":[{"name":"Breakfast","items":["4 egg whites","Oats 60g"]},{"name":"Lunch","items":["Chicken 200g","Rice 150g"]}]}	t	2026-04-01 11:00:00+00
\.


--
-- Data for Name: enrollments; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.enrollments (id, name, email, phone, plan_name, original_price, discount_percent, coupon_code, final_price, razorpay_payment_id, razorpay_order_id, payment_status, created_at) FROM stdin;
9001	Alpha Tester	alpha@example.com	9990000001	3 MONTH KICKSTART	1900.00	20	DUMMY20	1520.00	pay_DUMMY0001	order_DUMMY0001	Paid	2026-01-10 09:30:00+00
9002	Bravo Singh	bravo@example.com	9990000002	6 MONTH TRANSFORMATION	3400.00	0		3400.00	pay_DUMMY0002	order_DUMMY0002	Pending	2026-02-14 09:30:00+00
1	Test	test@test.com	1234567890	3 MONTH KICKSTART	10.00	0		10.00	pay_T4MbpGZm2Wyxjt	order_T4MbN6Tgw6oIvc	Paid	2026-06-21 17:28:32+00
\.


--
-- Data for Name: workout_days; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.workout_days (id, plan_id, day_number, day_name) FROM stdin;
9001	9002	1	Day 1 - Push
9002	9002	2	Day 2 - Pull
9003	9003	1	Day 1 - Full Body
\.


--
-- Data for Name: workout_exercises; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.workout_exercises (id, day_id, exercise_name, sets_count, reps, youtube_url, notes, sort_order) FROM stdin;
9005	9001	Warm-up Mobility	1	5 min	\N	no sort_order set	\N
9001	9001	Barbell Bench Press	4	8-10	https://youtube.com/watch?v=dummy1	Control the descent	1
9002	9001	Overhead Press	3	10	https://youtube.com/watch?v=dummy2	\N	2
9003	9002	Deadlift	4	5	https://youtube.com/watch?v=dummy3	Neutral spine	1
9004	9003	Burpees	3	15	\N	\N	1
\.


--
-- Data for Name: workout_logs; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.workout_logs (id, user_email, month_no, week_no, day_id, exercise_id, set_no, completed, created_at) FROM stdin;
9001	alpha@example.com	1	1	9001	9001	1	t	2026-04-02 07:30:00+00
9002	alpha@example.com	1	1	9001	9002	1	t	2026-04-02 07:45:00+00
9003	alpha@example.com	1	1	9002	9003	1	f	2026-04-03 07:30:00+00
1	arup.theorem@gmail.com	1	1	3	4	1	t	2026-06-15 11:25:44+00
\.


--
-- Data for Name: workout_plans; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.workout_plans (id, client_id, plan_name, is_active, created_at, workout_json, version_no) FROM stdin;
9001	9001	Foundation Phase (superseded)	f	2026-01-10 10:00:00+00	{"plan_name":"Foundation Phase","days":[]}	1
9002	9001	Strength Block	t	2026-04-01 10:00:00+00	{"plan_name":"Strength Block","days":[{"day_name":"Push"},{"day_name":"Pull"}]}	2
9003	9002	Fat Loss Kickstart	t	2026-02-14 10:00:00+00	{"plan_name":"Fat Loss Kickstart","days":[{"day_name":"Full Body"}]}	1
\.


--
-- Data for Name: workout_progress; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.workout_progress (id, client_id, exercise_id, set_number, completed, completed_at) FROM stdin;
9001	9001	9001	1	t	2026-04-02 07:30:00+00
\.


--
-- Name: affiliate_codes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.affiliate_codes_id_seq', 9003, true);


--
-- Name: affiliate_conversions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.affiliate_conversions_id_seq', 1, false);


--
-- Name: client_progress_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.client_progress_id_seq', 9002, true);


--
-- Name: clients_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.clients_id_seq', 9003, true);


--
-- Name: diet_plans_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.diet_plans_id_seq', 1, false);


--
-- Name: enrollments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.enrollments_id_seq', 9002, true);


--
-- Name: workout_days_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.workout_days_id_seq', 1, false);


--
-- Name: workout_exercises_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.workout_exercises_id_seq', 1, false);


--
-- Name: workout_logs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.workout_logs_id_seq', 9003, true);


--
-- Name: workout_plans_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.workout_plans_id_seq', 1, false);


--
-- Name: workout_progress_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.workout_progress_id_seq', 1, false);


--
-- Name: affiliate_codes affiliate_codes_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.affiliate_codes
    ADD CONSTRAINT affiliate_codes_code_key UNIQUE (code);


--
-- Name: affiliate_codes affiliate_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.affiliate_codes
    ADD CONSTRAINT affiliate_codes_pkey PRIMARY KEY (id);


--
-- Name: affiliate_conversions affiliate_conversions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.affiliate_conversions
    ADD CONSTRAINT affiliate_conversions_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: client_progress client_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.client_progress
    ADD CONSTRAINT client_progress_pkey PRIMARY KEY (id);


--
-- Name: clients clients_access_token_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_access_token_key UNIQUE (access_token);


--
-- Name: clients clients_email_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_email_key UNIQUE (email);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: diet_plans diet_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.diet_plans
    ADD CONSTRAINT diet_plans_pkey PRIMARY KEY (id);


--
-- Name: enrollments enrollments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.enrollments
    ADD CONSTRAINT enrollments_pkey PRIMARY KEY (id);


--
-- Name: workout_days workout_days_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_days
    ADD CONSTRAINT workout_days_pkey PRIMARY KEY (id);


--
-- Name: workout_exercises workout_exercises_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_exercises
    ADD CONSTRAINT workout_exercises_pkey PRIMARY KEY (id);


--
-- Name: workout_logs workout_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_logs
    ADD CONSTRAINT workout_logs_pkey PRIMARY KEY (id);


--
-- Name: workout_plans workout_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_plans
    ADD CONSTRAINT workout_plans_pkey PRIMARY KEY (id);


--
-- Name: workout_progress workout_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workout_progress
    ADD CONSTRAINT workout_progress_pkey PRIMARY KEY (id);


--
-- Name: ix_client_progress_client_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_client_progress_client_created ON public.client_progress USING btree (client_id, created_at);


--
-- Name: ix_diet_plans_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_diet_plans_active ON public.diet_plans USING btree (client_id, id) WHERE (is_active IS TRUE);


--
-- Name: ix_enrollments_coupon_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_enrollments_coupon_status ON public.enrollments USING btree (coupon_code, payment_status);


--
-- Name: ix_enrollments_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_enrollments_email ON public.enrollments USING btree (email);


--
-- Name: ix_enrollments_payment_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_enrollments_payment_id ON public.enrollments USING btree (razorpay_payment_id);


--
-- Name: ix_workout_days_plan_daynum; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_workout_days_plan_daynum ON public.workout_days USING btree (plan_id, day_number);


--
-- Name: ix_workout_exercises_day_sort; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_workout_exercises_day_sort ON public.workout_exercises USING btree (day_id, sort_order);


--
-- Name: ix_workout_logs_user_completed_ex; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_workout_logs_user_completed_ex ON public.workout_logs USING btree (user_email, exercise_id) WHERE (completed IS TRUE);


--
-- Name: ix_workout_logs_user_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_workout_logs_user_created ON public.workout_logs USING btree (user_email, created_at);


--
-- Name: ix_workout_plans_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_workout_plans_active ON public.workout_plans USING btree (client_id, id) WHERE (is_active IS TRUE);


--
-- Name: ix_workout_plans_client_version; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_workout_plans_client_version ON public.workout_plans USING btree (client_id, version_no);


--
-- PostgreSQL database dump complete
--

\unrestrict f9ZLaHP98e0Q60W9bJbsLqAtgZO44pPZQDeeCLIQh0Nh1iRbVZl87azojKO5fwf

