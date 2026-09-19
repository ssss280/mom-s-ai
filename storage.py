import sqlite3
import os
import json
import logging
from datetime import datetime

from paths import DATA_DIR, DB_PATH, ensure_writable_dir

logger = logging.getLogger(__name__)


class ChatStorage:
    def __init__(self, db_path: str = None):
        if db_path is None:
            # 冻结打包后必须用 exe 目录（或 CHATSIGHT_HOME），
            # 不能用 __file__，否则会算到 lib\library.zip 里面
            data_dir = ensure_writable_dir(DATA_DIR)
            db_path = os.path.join(data_dir, os.path.basename(DB_PATH))
        self.db_path = db_path
        logger.info(f"数据库路径: {db_path}")
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                image_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                suggestion_text TEXT NOT NULL,
                model_used TEXT,
                was_copied INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            );
        """)
        conn.commit()
        conn.close()

    def create_session(self, title: str = None) -> int:
        now = datetime.now().isoformat()
        if title is None:
            title = f"会话 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now)
        )
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return session_id

    def save_message(self, session_id: int, raw_text: str, image_path: str = None) -> int:
        now = datetime.now().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (session_id, raw_text, image_path, created_at) VALUES (?, ?, ?, ?)",
            (session_id, raw_text, image_path, now)
        )
        msg_id = cursor.lastrowid
        cursor.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id)
        )
        conn.commit()
        conn.close()
        return msg_id

    def save_suggestions(self, message_id: int, suggestions: list, model_used: str = "") -> list:
        now = datetime.now().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        ids = []
        for text in suggestions:
            cursor.execute(
                "INSERT INTO suggestions (message_id, suggestion_text, model_used, created_at) VALUES (?, ?, ?, ?)",
                (message_id, text, model_used, now)
            )
            ids.append(cursor.lastrowid)
        conn.commit()
        conn.close()
        return ids

    def mark_suggestion_copied(self, suggestion_id: int):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE suggestions SET was_copied = 1 WHERE id = ?", (suggestion_id,))
        conn.commit()
        conn.close()

    def get_sessions(self, limit: int = 50) -> list:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]

    def get_session_messages(self, session_id: int) -> list:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, raw_text, image_path, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,)
        )
        messages = []
        for row in cursor.fetchall():
            msg_id, raw_text, image_path, created_at = row
            cursor2 = conn.cursor()
            cursor2.execute(
                "SELECT id, suggestion_text, model_used, was_copied, created_at FROM suggestions WHERE message_id = ?",
                (msg_id,)
            )
            suggestions = [
                {"id": s[0], "text": s[1], "model": s[2], "copied": bool(s[3]), "created_at": s[4]}
                for s in cursor2.fetchall()
            ]
            messages.append({
                "id": msg_id,
                "raw_text": raw_text,
                "image_path": image_path,
                "created_at": created_at,
                "suggestions": suggestions,
            })
        conn.close()
        return messages

    def search_messages(self, keyword: str, limit: int = 20) -> list:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT m.id, m.raw_text, m.created_at, s.title
               FROM messages m JOIN sessions s ON m.session_id = s.id
               WHERE m.raw_text LIKE ? ORDER BY m.created_at DESC LIMIT ?""",
            (f"%{keyword}%", limit)
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"id": r[0], "text": r[1], "created_at": r[2], "session_title": r[3]}
            for r in rows
        ]

    def delete_session(self, session_id: int):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM suggestions WHERE message_id IN (SELECT id FROM messages WHERE session_id = ?)", (session_id,))
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
