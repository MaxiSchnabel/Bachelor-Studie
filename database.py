import sqlite3
import os
import uuid
from datetime import datetime

if os.path.isdir("/data"):
    DB_PATH = "/data/study.db"
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "study.db")

STRATEGY_ORDERS = [
    ["additive",        "least_misery",   "majority_voting"],
    ["additive",        "majority_voting", "least_misery"],
    ["least_misery",    "additive",        "majority_voting"],
    ["least_misery",    "majority_voting", "additive"],
    ["majority_voting", "additive",        "least_misery"],
    ["majority_voting", "least_misery",    "additive"],
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
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
            submitted_at            TEXT NOT NULL
        );
        """)


def create_participant():
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        order_index = count % 6
        pid = str(uuid.uuid4())[:8].upper()
        conn.execute(
            "INSERT INTO participants (id, created_at, order_index) VALUES (?, ?, ?)",
            (pid, datetime.now().isoformat(), order_index)
        )
    return pid, order_index


def save_preferences(participant_id, dietary, cuisine, spice_level):
    with get_db() as conn:
        conn.execute(
            "UPDATE participants SET dietary=?, cuisine=?, spice_level=? WHERE id=?",
            (dietary, cuisine, spice_level, participant_id)
        )


def save_response(participant_id, round_num, strategy, recipes_shown, form_data):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO responses
            (participant_id, round, strategy, recipes_shown,
             fair_1, fair_2, fair_3,
             sat_1, sat_2, sat_3, sat_4,
             appeal_1, appeal_2, submitted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            participant_id, round_num, strategy, recipes_shown,
            form_data.get("fair_1"), form_data.get("fair_2"), form_data.get("fair_3"),
            form_data.get("sat_1"),  form_data.get("sat_2"),
            form_data.get("sat_3"),  form_data.get("sat_4"),
            form_data.get("appeal_1"), form_data.get("appeal_2"),
            datetime.now().isoformat()
        ))


def save_demographics(participant_id, form_data):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO demographics
            (participant_id, age, gender, recommender_experience, submitted_at)
            VALUES (?,?,?,?,?)
        """, (
            participant_id,
            form_data.get("age"),
            form_data.get("gender"),
            form_data.get("recommender_experience"),
            datetime.now().isoformat()
        ))
        conn.execute(
            "UPDATE participants SET completed=1 WHERE id=?",
            (participant_id,)
        )


def export_csv():
    import csv, io
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                p.id, p.created_at, p.order_index, p.completed,
                p.dietary, p.cuisine, p.spice_level,
                r.round, r.strategy, r.recipes_shown,
                r.fair_1, r.fair_2, r.fair_3,
                r.sat_1, r.sat_2, r.sat_3, r.sat_4,
                r.appeal_1, r.appeal_2, r.submitted_at,
                d.age, d.gender, d.recommender_experience
            FROM participants p
            LEFT JOIN responses r ON p.id = r.participant_id
            LEFT JOIN demographics d ON p.id = d.participant_id
            ORDER BY p.created_at, r.round
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "participant_id","created_at","order_index","completed",
        "dietary","cuisine","spice_level",
        "round","strategy","recipes_shown",
        "fair_1","fair_2","fair_3",
        "sat_1","sat_2","sat_3","sat_4",
        "appeal_1","appeal_2","response_submitted_at",
        "age","gender","recommender_experience"
    ])
    writer.writerows(rows)
    return output.getvalue()


def get_stats():
    with get_db() as conn:
        total     = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM participants WHERE completed=1").fetchone()[0]
        orders    = conn.execute(
            "SELECT order_index, COUNT(*) as n FROM participants GROUP BY order_index"
        ).fetchall()
    return {"total": total, "completed": completed, "orders": orders}
