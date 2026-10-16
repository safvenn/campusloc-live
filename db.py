"""Database and Authentication module for CampusLoc.
Supports PostgreSQL (Render/Supabase) and SQLite with automatic schema creation.
"""
import hashlib
import hmac
import os
import sqlite3
import time
from pathlib import Path
import jwt

ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.getenv('DATABASE_URL', '')
JWT_SECRET = os.getenv('JWT_SECRET', 'campusloc-super-secret-key-2026-experimental')
JWT_ALGORITHM = 'HS256'

IS_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith(('postgres://', 'postgresql://')))
if IS_POSTGRES and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)


def get_connection():
    if IS_POSTGRES:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(DATABASE_URL)
    else:
        db_path = ROOT / 'campusloc.db'
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
    return f"{salt}:{hashed}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, hashed = stored_hash.split(':')
        verify = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
        return hmac.compare_digest(hashed, verify)
    except Exception:
        return False


def create_token(user: dict) -> str:
    payload = {
        'sub': str(user['id']),
        'email': user['email'],
        'name': user['name'],
        'role': user.get('role', 'user'),
        'exp': int(time.time()) + 86400 * 7  # 7 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    try:
        if IS_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(50) DEFAULT 'user',
                    created_at DOUBLE PRECISION NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS floor_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    user_email VARCHAR(255),
                    user_name VARCHAR(255),
                    device_id VARCHAR(64) NOT NULL,
                    floor VARCHAR(32) NOT NULL,
                    started_at DOUBLE PRECISION NOT NULL,
                    ended_at DOUBLE PRECISION NOT NULL,
                    duration_seconds DOUBLE PRECISION NOT NULL,
                    last_heartbeat DOUBLE PRECISION NOT NULL
                );
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT DEFAULT 'user',
                    created_at REAL NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS floor_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_email TEXT,
                    user_name TEXT,
                    device_id TEXT NOT NULL,
                    floor TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    duration_seconds REAL NOT NULL,
                    last_heartbeat REAL NOT NULL
                );
            """)
        conn.commit()

        # Seed default Admin account if not exists
        cur.execute("SELECT id FROM users WHERE email = %s" if IS_POSTGRES else "SELECT id FROM users WHERE email = ?",
                    ('admin@campusloc.internal',))
        if not cur.fetchone():
            now = time.time()
            admin_pwd = hash_password('admin123')
            if IS_POSTGRES:
                cur.execute("""
                    INSERT INTO users (email, name, password_hash, role, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, ('admin@campusloc.internal', 'Administrator', admin_pwd, 'admin', now))
            else:
                cur.execute("""
                    INSERT INTO users (email, name, password_hash, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, ('admin@campusloc.internal', 'Administrator', admin_pwd, 'admin', now))
            conn.commit()
    finally:
        cur.close()
        conn.close()


def create_user(email: str, name: str, password: str, role: str = 'user') -> dict:
    email = email.strip().lower()
    name = name.strip()
    if not email or '@' not in email:
        raise ValueError('Invalid email format')
    if len(name) < 2:
        raise ValueError('Name must have at least 2 characters')
    if len(password) < 6:
        raise ValueError('Password must have at least 6 characters')

    conn = get_connection()
    cur = conn.cursor()
    try:
        now = time.time()
        pwd_hash = hash_password(password)
        if IS_POSTGRES:
            cur.execute("""
                INSERT INTO users (email, name, password_hash, role, created_at)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (email, name, pwd_hash, role, now))
            user_id = cur.fetchone()[0]
        else:
            cur.execute("""
                INSERT INTO users (email, name, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (email, name, pwd_hash, role, now))
            user_id = cur.lastrowid
        conn.commit()
        return {'id': user_id, 'email': email, 'name': name, 'role': role}
    finally:
        cur.close()
        conn.close()


def authenticate_user(email: str, password: str) -> dict | None:
    email = email.strip().lower()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if IS_POSTGRES:
            cur.execute("SELECT id, email, name, password_hash, role FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if row and verify_password(password, row[3]):
                return {'id': row[0], 'email': row[1], 'name': row[2], 'role': row[4]}
        else:
            cur.execute("SELECT id, email, name, password_hash, role FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            if row and verify_password(password, row['password_hash']):
                return {'id': row['id'], 'email': row['email'], 'name': row['name'], 'role': row['role']}
        return None
    finally:
        cur.close()
        conn.close()


def close_user_session(device_id: str, now=None):
    """Closes any active floor dwell session when a user goes Out of Box or disconnects."""
    now = time.time() if now is None else now
    conn = get_connection()
    cur = conn.cursor()
    try:
        if IS_POSTGRES:
            cur.execute("""
                SELECT id, started_at, last_heartbeat FROM floor_sessions
                WHERE device_id = %s ORDER BY id DESC LIMIT 1
            """, (device_id,))
            latest = cur.fetchone()
            if latest and (now - latest[2]) <= 90:
                duration = round(now - latest[1], 1)
                cur.execute("UPDATE floor_sessions SET ended_at = %s, duration_seconds = %s WHERE id = %s",
                            (now, duration, latest[0]))
                conn.commit()
        else:
            cur.execute("""
                SELECT id, started_at, last_heartbeat FROM floor_sessions
                WHERE device_id = ? ORDER BY id DESC LIMIT 1
            """, (device_id,))
            latest = cur.fetchone()
            if latest and (now - latest['last_heartbeat']) <= 90:
                duration = round(now - latest['started_at'], 1)
                cur.execute("UPDATE floor_sessions SET ended_at = ?, duration_seconds = ? WHERE id = ?",
                            (now, duration, latest['id']))
                conn.commit()
    finally:
        cur.close()
        conn.close()


def record_live_heartbeat(device_id: str, floor: str, user_id=None, user_name=None, user_email=None, now=None):
    """Tracks time spent on a floor continuously. Does NOT record Out of Box data."""
    now = time.time() if now is None else now
    # DO NOT RECORD OUT OF BOX DATA
    if not floor or str(floor).strip().lower() in ('out of box', 'out_of_box', 'cannot_localize', 'unlocated', 'none', '—'):
        close_user_session(device_id, now)
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        # Find active session for this device/user
        if IS_POSTGRES:
            cur.execute("""
                SELECT id, floor, started_at, last_heartbeat FROM floor_sessions
                WHERE device_id = %s ORDER BY id DESC LIMIT 1
            """, (device_id,))
            latest = cur.fetchone()
        else:
            cur.execute("""
                SELECT id, floor, started_at, last_heartbeat FROM floor_sessions
                WHERE device_id = ? ORDER BY id DESC LIMIT 1
            """, (device_id,))
            latest = cur.fetchone()

        if latest:
            sess_id = latest[0] if IS_POSTGRES else latest['id']
            prev_floor = latest[1] if IS_POSTGRES else latest['floor']
            started_at = latest[2] if IS_POSTGRES else latest['started_at']
            last_hb = latest[3] if IS_POSTGRES else latest['last_heartbeat']

            # If same floor and last heartbeat was within 90 seconds, extend session
            if prev_floor.lower() == floor.lower() and (now - last_hb) <= 90:
                duration = round(now - started_at, 1)
                if IS_POSTGRES:
                    cur.execute("""
                        UPDATE floor_sessions
                        SET ended_at = %s, duration_seconds = %s, last_heartbeat = %s,
                            user_id = COALESCE(%s, user_id),
                            user_name = COALESCE(%s, user_name),
                            user_email = COALESCE(%s, user_email)
                        WHERE id = %s
                    """, (now, duration, now, user_id, user_name, user_email, sess_id))
                else:
                    cur.execute("""
                        UPDATE floor_sessions
                        SET ended_at = ?, duration_seconds = ?, last_heartbeat = ?,
                            user_id = COALESCE(?, user_id),
                            user_name = COALESCE(?, user_name),
                            user_email = COALESCE(?, user_email)
                        WHERE id = ?
                    """, (now, duration, now, user_id, user_name, user_email, sess_id))
                conn.commit()
                return

        # Otherwise start a brand new floor dwell session
        if IS_POSTGRES:
            cur.execute("""
                INSERT INTO floor_sessions
                (user_id, user_email, user_name, device_id, floor, started_at, ended_at, duration_seconds, last_heartbeat)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, user_email, user_name, device_id, floor, now, now, 0.0, now))
        else:
            cur.execute("""
                INSERT INTO floor_sessions
                (user_id, user_email, user_name, device_id, floor, started_at, ended_at, duration_seconds, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, user_email, user_name, device_id, floor, now, now, 0.0, now))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_floor_analytics() -> dict:
    """Returns analytics: total time spent per floor and per user."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Time spent per floor
        cur.execute("""
            SELECT floor, SUM(duration_seconds) AS total_sec, COUNT(*) AS session_count
            FROM floor_sessions
            GROUP BY floor
        """)
        floor_rows = cur.fetchall()

        floor_summary = {}
        floors_all = ['B1', 'Ground', 'First', 'Second', 'Third', 'Fourth']
        for f in floors_all:
            floor_summary[f] = {'total_seconds': 0.0, 'total_minutes': 0.0, 'session_count': 0}

        for r in floor_rows:
            f = r[0] if IS_POSTGRES else r['floor']
            sec = r[1] if IS_POSTGRES else r['total_sec']
            cnt = r[2] if IS_POSTGRES else r['session_count']
            sec = round(float(sec or 0), 1)
            f_norm = f.capitalize() if f.lower() != 'b1' else 'B1'
            if f_norm in floor_summary:
                floor_summary[f_norm]['total_seconds'] += sec
                floor_summary[f_norm]['total_minutes'] = round(floor_summary[f_norm]['total_seconds'] / 60, 1)
                floor_summary[f_norm]['session_count'] += int(cnt or 0)

        # Time spent per user & floor
        cur.execute("""
            SELECT COALESCE(user_name, device_id) AS display_name,
                   COALESCE(user_email, 'Guest/Anonymous') AS email,
                   floor,
                   SUM(duration_seconds) AS total_sec,
                   MAX(ended_at) AS last_active
            FROM floor_sessions
            GROUP BY display_name, email, floor
            ORDER BY last_active DESC
        """)
        user_rows = cur.fetchall()

        user_breakdown = []
        for r in user_rows:
            user_breakdown.append({
                'display_name': r[0] if IS_POSTGRES else r['display_name'],
                'email': r[1] if IS_POSTGRES else r['email'],
                'floor': r[2] if IS_POSTGRES else r['floor'],
                'total_seconds': round(float(r[3] if IS_POSTGRES else r['total_sec'] or 0), 1),
                'total_minutes': round(float(r[3] if IS_POSTGRES else r['total_sec'] or 0) / 60, 1),
                'last_active': r[4] if IS_POSTGRES else r['last_active']
            })

        # Recent sessions
        cur.execute("""
            SELECT id, user_name, user_email, device_id, floor, started_at, ended_at, duration_seconds
            FROM floor_sessions
            ORDER BY id DESC LIMIT 50
        """)
        recent_rows = cur.fetchall()
        recent_sessions = []
        for r in recent_rows:
            recent_sessions.append({
                'id': r[0] if IS_POSTGRES else r['id'],
                'user_name': r[1] if IS_POSTGRES else r['user_name'] or 'Guest',
                'user_email': r[2] if IS_POSTGRES else r['user_email'] or 'Guest',
                'device_id': r[3] if IS_POSTGRES else r['device_id'],
                'floor': r[4] if IS_POSTGRES else r['floor'],
                'started_at': r[5] if IS_POSTGRES else r['started_at'],
                'ended_at': r[6] if IS_POSTGRES else r['ended_at'],
                'duration_seconds': round(float(r[7] if IS_POSTGRES else r['duration_seconds'] or 0), 1)
            })

        return {
            'floor_summary': floor_summary,
            'user_breakdown': user_breakdown,
            'recent_sessions': recent_sessions
        }
    finally:
        cur.close()
        conn.close()


init_db()
