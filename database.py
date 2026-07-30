import os
import uuid
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES = True
except ImportError:
    import sqlite3
    POSTGRES = False

# SQLite fallback for local development
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "study.db")

STRATEGY_ORDERS = [
    ["additive",        "least_misery",   "majority_voting"],
    ["additive",        "majority_voting", "least_misery"],
    ["least_misery",    "additive",        "majority_voting"],
    ["least_misery",    "majority_voting", "additive"],
    ["majority_voting", "additive",        "least_misery"],
    ["majority_voting", "least_misery",    "additive"],
]


def get_db():
    """Return a database connection — PostgreSQL on Render, SQLite locally."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url and POSTGRES:
        # Render provides postgres:// but psycopg2 needs postgresql://
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(db_url)
        return conn
    else:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def is_postgres():
    return bool(os.environ.get("DATABASE_URL")) and POSTGRES


def placeholder(n=1):
    """Return correct placeholders for the current DB."""
    if is_postgres():
        return ", ".join(["%s"] * n)
    return ", ".join(["?"] * n)


def ph(n=1):
    """Short alias for placeholder."""
    return placeholder(n)


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    try:
        cur = conn.cursor()
        if is_postgres():
            cur.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id          TEXT PRIMARY KEY,
                    created_at  TEXT NOT NULL,
                    order_index INTEGER NOT NULL,
                    dietary     TEXT,
                    cuisine     TEXT,
                    spice_level TEXT,
                    completed   INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS responses (
                    id              SERIAL PRIMARY KEY,
                    participant_id  TEXT NOT NULL,
                    round           INTEGER NOT NULL,
                    strategy        TEXT NOT NULL,
                    recipes_shown   TEXT,
                    fair_1          INTEGER,
                    fair_2          INTEGER,
                    fair_3          INTEGER,
                    sat_1           INTEGER,
                    sat_2           INTEGER,
                    sat_3           INTEGER,
                    sat_4           INTEGER,
                    appeal_1        INTEGER,
                    appeal_2        INTEGER,
                    submitted_at    TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS demographics (
                    participant_id          TEXT PRIMARY KEY,
                    age                     INTEGER,
                    gender                  TEXT,
                    recommender_experience  TEXT,
                    matrikelnummer          TEXT,
                    submitted_at            TEXT NOT NULL
                )
            """)
        else:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS participants (
                    id          TEXT PRIMARY KEY,
                    created_at  TEXT NOT NULL,
                    order_index INTEGER NOT NULL,
                    dietary     TEXT,
                    cuisine     TEXT,
                    spice_level TEXT,
                    completed   INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS responses (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id  TEXT NOT NULL,
                    round           INTEGER NOT NULL,
                    strategy        TEXT NOT NULL,
                    recipes_shown   TEXT,
                    fair_1          INTEGER,
                    fair_2          INTEGER,
                    fair_3          INTEGER,
                    sat_1           INTEGER,
                    sat_2           INTEGER,
                    sat_3           INTEGER,
                    sat_4           INTEGER,
                    appeal_1        INTEGER,
                    appeal_2        INTEGER,
                    submitted_at    TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demographics (
                    participant_id          TEXT PRIMARY KEY,
                    age                     INTEGER,
                    gender                  TEXT,
                    recommender_experience  TEXT,
                    matrikelnummer          TEXT,
                    submitted_at            TEXT NOT NULL
                );
            """)
        conn.commit()
    finally:
        conn.close()


def create_participant():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM participants")
        row = cur.fetchone()
        count = row[0] if is_postgres() else row[0]
        order_index = count % 6
        pid = str(uuid.uuid4())[:8].upper()
        cur.execute(
            f"INSERT INTO participants (id, created_at, order_index) VALUES ({ph(3)})",
            (pid, datetime.now().isoformat(), order_index)
        )
        conn.commit()
    finally:
        conn.close()
    return pid, order_index


def save_preferences(participant_id, dietary, cuisine, spice_level):
    conn = get_db()
    try:
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                "UPDATE participants SET dietary=%s, cuisine=%s, spice_level=%s WHERE id=%s",
                (dietary, cuisine, spice_level, participant_id)
            )
        else:
            cur.execute(
                "UPDATE participants SET dietary=?, cuisine=?, spice_level=? WHERE id=?",
                (dietary, cuisine, spice_level, participant_id)
            )
        conn.commit()
    finally:
        conn.close()


def save_response(participant_id, round_num, strategy, recipes_shown, form_data):
    conn = get_db()
    try:
        cur = conn.cursor()
        vals = (
            participant_id, round_num, strategy, recipes_shown,
            form_data.get("fair_1"), form_data.get("fair_2"), form_data.get("fair_3"),
            form_data.get("sat_1"),  form_data.get("sat_2"),
            form_data.get("sat_3"),  form_data.get("sat_4"),
            form_data.get("appeal_1"), form_data.get("appeal_2"),
            datetime.now().isoformat()
        )
        if is_postgres():
            cur.execute("""
                INSERT INTO responses
                (participant_id, round, strategy, recipes_shown,
                 fair_1, fair_2, fair_3, sat_1, sat_2, sat_3, sat_4,
                 appeal_1, appeal_2, submitted_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, vals)
        else:
            cur.execute("""
                INSERT INTO responses
                (participant_id, round, strategy, recipes_shown,
                 fair_1, fair_2, fair_3, sat_1, sat_2, sat_3, sat_4,
                 appeal_1, appeal_2, submitted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, vals)
        conn.commit()
    finally:
        conn.close()


def save_demographics(participant_id, form_data):
    conn = get_db()
    try:
        cur = conn.cursor()
        vals = (
            participant_id,
            form_data.get("age"),
            form_data.get("gender"),
            form_data.get("recommender_experience"),
            form_data.get("matrikelnummer") or None,
            datetime.now().isoformat()
        )
        if is_postgres():
            cur.execute("""
                INSERT INTO demographics
                (participant_id, age, gender, recommender_experience,
                 matrikelnummer, submitted_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (participant_id) DO UPDATE
                SET age=EXCLUDED.age, gender=EXCLUDED.gender,
                    recommender_experience=EXCLUDED.recommender_experience,
                    matrikelnummer=EXCLUDED.matrikelnummer,
                    submitted_at=EXCLUDED.submitted_at
            """, vals)
            cur.execute(
                "UPDATE participants SET completed=1 WHERE id=%s",
                (participant_id,)
            )
        else:
            cur.execute("""
                INSERT OR REPLACE INTO demographics
                (participant_id, age, gender, recommender_experience,
                 matrikelnummer, submitted_at)
                VALUES (?,?,?,?,?,?)
            """, vals)
            cur.execute(
                "UPDATE participants SET completed=1 WHERE id=?",
                (participant_id,)
            )
        conn.commit()
    finally:
        conn.close()


def export_csv():
    import csv, io
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                p.id, p.created_at, p.order_index, p.completed,
                p.dietary, p.cuisine, p.spice_level,
                r.round, r.strategy, r.recipes_shown,
                r.fair_1, r.fair_2, r.fair_3,
                r.sat_1, r.sat_2, r.sat_3, r.sat_4,
                r.appeal_1, r.appeal_2, r.submitted_at,
                d.age, d.gender, d.recommender_experience, d.matrikelnummer
            FROM participants p
            LEFT JOIN responses r ON p.id = r.participant_id
            LEFT JOIN demographics d ON p.id = d.participant_id
            ORDER BY p.created_at, r.round
        """)
        rows = cur.fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "participant_id","created_at","order_index","completed",
        "dietary","cuisine","spice_level",
        "round","strategy","recipes_shown",
        "fair_1","fair_2","fair_3",
        "sat_1","sat_2","sat_3","sat_4",
        "appeal_1","appeal_2","response_submitted_at",
        "age","gender","recommender_experience","matrikelnummer"
    ])
    writer.writerows(rows)
    return output.getvalue()


def get_stats():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM participants")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM participants WHERE completed=1")
        completed = cur.fetchone()[0]
        cur.execute(
            "SELECT order_index, COUNT(*) as n FROM participants GROUP BY order_index"
        )
        orders = cur.fetchall()
    finally:
        conn.close()
    return {"total": total, "completed": completed, "orders": orders}