import sqlite3
import os
import json
from datetime import datetime

class JobDatabase:
    def __init__(self, db_path="storage/jobs.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _ensure_column(self, conn, table_name: str, column_name: str, column_type: str):
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _init_db(self):
        if not os.path.exists(os.path.dirname(self.db_path)):
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
        with self._get_connection() as conn:
            # Jobs History
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT UNIQUE,
                    title TEXT UNIQUE,
                    text TEXT,
                    niche TEXT,
                    voice_id TEXT,
                    status TEXT,
                    video_url TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP
                )
            """)
            # Gallery / Media Library
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    filename TEXT,
                    original_name TEXT,
                    file_type TEXT,
                    file_path TEXT,
                    size_bytes INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # API Settings
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key_name TEXT PRIMARY KEY,
                    key_value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # AI Templates
            conn.execute("""
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    prompt TEXT,
                    style TEXT
                )
            """)
            
            # Insert default templates if they don't exist
            cursor = conn.execute("SELECT COUNT(*) FROM templates")
            if cursor.fetchone()[0] == 0:
                conn.executemany("INSERT INTO templates (name, prompt, style) VALUES (?, ?, ?)", [
                    ("Terror", "Convierte este texto en una historia de terror corta, usa lenguaje lúgubre y añade pausas dramáticas con puntos suspensivos.", "dark"),
                    ("Curiosidad", "Haz que este texto sea intrigante y educativo. Usa un lenguaje que despierte la curiosidad rápidamente.", "educational"),
                    ("Motivacional", "Dale un tono inspirador y épico a este texto. Usa frases cortas y potentes.", "epic")
                ])
            
                
            try:
                self._ensure_column(conn, "media", "channel_id", "INTEGER")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "scenes_json", "TEXT")
            except Exception:
                pass  # La columna ya existe
            
            try:
                self._ensure_column(conn, "jobs", "music_filename", "TEXT")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "music_volume", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "voice_volume", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "intro_fade_duration", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "outro_fade_duration", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "music_fade_out_duration", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "tail_silence_seconds", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "tts_engine", "TEXT")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "tts_speed", "REAL")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "youtube_video_id", "TEXT")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "youtube_video_url", "TEXT")
            except Exception:
                pass

            try:
                self._ensure_column(conn, "jobs", "youtube_published_at", "TIMESTAMP")
            except Exception:
                pass

            # Migration: add unique title column to existing DBs
            try:
                self._ensure_column(conn, "jobs", "title", "TEXT")
            except Exception:
                pass  # Column already exists
            try:
                self._ensure_column(conn, "jobs", "channel_id", "INTEGER")
            except Exception:
                pass
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_title ON jobs(title) WHERE title IS NOT NULL")
            except Exception:
                pass
            
            # AI Assets Bank
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER UNIQUE,
                    prompt TEXT,
                    niche TEXT,
                    model TEXT,
                    asset_tag TEXT,
                    is_ai INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
                )
            """)

            # AI Generation Tasks (Status Tracking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE,
                    channel_id INTEGER,
                    prompt TEXT,
                    niche TEXT,
                    model TEXT,
                    status TEXT, -- pending, processing, completed, failed
                    result_url TEXT,
                    media_id INTEGER,
                    batch_id TEXT, -- Relation to ai_batches
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP
                )
            """)

            # Candidate News (for Discovery / Discovery Dashboard)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT UNIQUE,
                    content TEXT,
                    viral_score INTEGER,
                    source TEXT,
                    niche TEXT,
                    voice_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # AI Batches (Grouping tasks for n8n/automation)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT UNIQUE,
                    status TEXT, -- processing, completed, failed, partial
                    total_tasks INTEGER,
                    completed_tasks INTEGER DEFAULT 0,
                    failed_tasks INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    channel_id INTEGER,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'info',
                    message TEXT NOT NULL,
                    details_json TEXT,
                    scene_id TEXT,
                    actor TEXT DEFAULT 'system',
                    duration_ms INTEGER,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration: Ensure UNIQUE constraint on existing table if it was created without it
            try:
                # Deduplicate if necessary before adding constraint (sqlite doesn't support easy ALTER for this)
                # We'll just try to create a unique index which effectively acts as a constraint
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_assets_media_id ON ai_assets(media_id)")
                # Add batch_id and channel_id to ai_tasks if missing
                self._ensure_column(conn, "ai_tasks", "batch_id", "TEXT")
                self._ensure_column(conn, "ai_tasks", "channel_id", "INTEGER")
                self._ensure_column(conn, "job_logs", "channel_id", "INTEGER")
                self._ensure_column(conn, "job_logs", "details_json", "TEXT")
                self._ensure_column(conn, "job_logs", "scene_id", "TEXT")
                self._ensure_column(conn, "job_logs", "actor", "TEXT")
                self._ensure_column(conn, "job_logs", "duration_ms", "INTEGER")
                self._ensure_column(conn, "job_logs", "error_code", "TEXT")
                self._ensure_column(conn, "job_logs", "error_message", "TEXT")
            except Exception as e:
                # If column already exists or other error, ignore
                pass

            # YouTube connected channels
            conn.execute("""
                CREATE TABLE IF NOT EXISTS youtube_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    internal_name TEXT NOT NULL,
                    internal_description TEXT,
                    google_client_id TEXT,
                    google_client_secret TEXT,
                    google_redirect_uri TEXT,
                    youtube_channel_id TEXT,
                    youtube_channel_title TEXT,
                    youtube_channel_handle TEXT,
                    youtube_channel_url TEXT,
                    thumbnail_url TEXT,
                    subscriber_count INTEGER,
                    connected_google_email TEXT,
                    default_privacy_status TEXT NOT NULL DEFAULT 'private',
                    default_category_id TEXT NOT NULL DEFAULT '22',
                    default_tags TEXT,
                    default_language TEXT NOT NULL DEFAULT 'es',
                    notify_subscribers INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'inactive',
                    connection_status TEXT NOT NULL DEFAULT 'disconnected',
                    scopes_granted TEXT,
                    access_token_encrypted TEXT,
                    refresh_token_encrypted TEXT,
                    token_expires_at TIMESTAMP,
                    last_connection_test_at TIMESTAMP,
                    last_connection_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for column_def in [
                ("google_client_id", "TEXT"),
                ("google_client_secret", "TEXT"),
                ("google_redirect_uri", "TEXT"),
                ("subscriber_count", "INTEGER"),
                ("view_count", "INTEGER"),
                ("video_count", "INTEGER"),
            ]:
                try:
                    self._ensure_column(conn, "youtube_channels", column_def[0], column_def[1])
                except Exception:
                    pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS youtube_oauth_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state TEXT UNIQUE NOT NULL,
                    channel_id INTEGER NOT NULL,
                    redirect_uri TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    consumed_at TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE
                )
            """)

            try:
                self._ensure_column(conn, "youtube_oauth_states", "redirect_uri", "TEXT")
            except Exception:
                pass

            # Guiones / research tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS script_topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS script_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    source_url TEXT,
                    youtube_video_id TEXT,
                    title TEXT,
                    thumbnail_url TEXT,
                    source_type TEXT DEFAULT 'youtube',
                    language TEXT,
                    raw_text TEXT,
                    translated_text TEXT,
                    summary TEXT,
                    apify_run_id TEXT,
                    apify_dataset_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (topic_id) REFERENCES script_topics(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS script_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER NOT NULL,
                    version INTEGER DEFAULT 1,
                    draft_type TEXT DEFAULT 'outline',
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (topic_id) REFERENCES script_topics(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS script_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER,
                    source_id INTEGER,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for table_name, columns in [
                ("script_topics", [("channel_id", "INTEGER"), ("title", "TEXT"), ("topic", "TEXT"), ("status", "TEXT"), ("updated_at", "TIMESTAMP")]),
                ("script_sources", [("topic_id", "INTEGER"), ("channel_id", "INTEGER"), ("source_url", "TEXT"), ("youtube_video_id", "TEXT"), ("title", "TEXT"), ("thumbnail_url", "TEXT"), ("source_type", "TEXT"), ("language", "TEXT"), ("raw_text", "TEXT"), ("translated_text", "TEXT"), ("summary", "TEXT"), ("apify_run_id", "TEXT"), ("apify_dataset_id", "TEXT"), ("updated_at", "TIMESTAMP")]),
                ("script_drafts", [("topic_id", "INTEGER"), ("version", "INTEGER"), ("draft_type", "TEXT"), ("content", "TEXT"), ("updated_at", "TIMESTAMP")]),
                ("script_logs", [("topic_id", "INTEGER"), ("source_id", "INTEGER"), ("event_type", "TEXT"), ("message", "TEXT"), ("details_json", "TEXT")]),
            ]:
                for column_name, column_type in columns:
                    try:
                        self._ensure_column(conn, table_name, column_name, column_type)
                    except Exception:
                        pass
                 
            conn.commit()

            # Initialize Kie Keys if missing
            cursor = conn.execute("SELECT COUNT(*) FROM settings WHERE key_name = 'KIE_API_KEY_1'")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO settings (key_name, key_value) VALUES (?, ?)", ("KIE_API_KEY_1", "a89431fe9e4cea7f92f2b969d94f3a4c"))
                conn.execute("INSERT INTO settings (key_name, key_value) VALUES (?, ?)", ("KIE_CURRENT_KEY_INDEX", "1"))
                conn.commit()

            cursor = conn.execute("SELECT COUNT(*) FROM settings WHERE key_name = 'APIFY_CURRENT_KEY_INDEX'")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO settings (key_name, key_value) VALUES (?, ?)", ("APIFY_CURRENT_KEY_INDEX", "1"))
                conn.commit()

    # Multimedia Gallery Methods
    def add_media(self, filename, original_name, file_type, file_path, size_bytes, channel_id=None):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO media (channel_id, filename, original_name, file_type, file_path, size_bytes) VALUES (?, ?, ?, ?, ?, ?)",
                (channel_id, filename, original_name, file_type, file_path, size_bytes)
            )
            media_id = cursor.lastrowid
            conn.commit()
            return media_id

    # Generic Config Settings Methods
    def get_setting(self, key_name: str, default=None):
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT key_value FROM settings WHERE key_name = ?", (key_name,))
            row = cursor.fetchone()
            if row:
                return row[0]
            return default

    def set_setting(self, key_name: str, key_value: str):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key_name, key_value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key_name, key_value)
            )
            conn.commit()

    # Script / Guiones methods
    def create_script_topic(self, channel_id: int, title: str, topic: str, status: str = "draft"):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO script_topics (channel_id, title, topic, status) VALUES (?, ?, ?, ?)",
                (channel_id, title, topic, status),
            )
            conn.commit()
            return cursor.lastrowid

    def get_script_topic(self, topic_id: int):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM script_topics WHERE id = ?", (topic_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_script_topics(self, channel_id=None, limit=50, offset=0, search=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM script_topics WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (title LIKE ? OR topic LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])
            query += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def find_script_source(self, topic_id: int, source_url=None, youtube_video_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if youtube_video_id:
                row = conn.execute(
                    "SELECT * FROM script_sources WHERE topic_id = ? AND youtube_video_id = ? ORDER BY id DESC LIMIT 1",
                    (topic_id, youtube_video_id),
                ).fetchone()
                if row:
                    return dict(row)
            if source_url:
                row = conn.execute(
                    "SELECT * FROM script_sources WHERE topic_id = ? AND source_url = ? ORDER BY id DESC LIMIT 1",
                    (topic_id, source_url),
                ).fetchone()
                if row:
                    return dict(row)
            return None

    def add_script_source(self, topic_id: int, source_url=None, youtube_video_id=None, title=None, thumbnail_url=None, source_type="youtube", language=None, raw_text=None, translated_text=None, summary=None, apify_run_id=None, apify_dataset_id=None, channel_id=None):
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO script_sources (
                    topic_id, channel_id, source_url, youtube_video_id, title, thumbnail_url, source_type, language,
                    raw_text, translated_text, summary, apify_run_id, apify_dataset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (topic_id, channel_id, source_url, youtube_video_id, title, thumbnail_url, source_type, language, raw_text, translated_text, summary, apify_run_id, apify_dataset_id),
            )
            conn.commit()
            return cursor.lastrowid

    def update_script_source(self, source_id: int, source_url=None, youtube_video_id=None, title=None, thumbnail_url=None, language=None, raw_text=None, translated_text=None, summary=None, apify_run_id=None, apify_dataset_id=None):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE script_sources
                SET source_url = ?,
                    youtube_video_id = ?,
                    title = ?,
                    thumbnail_url = ?,
                    language = ?,
                    raw_text = ?,
                    translated_text = ?,
                    summary = ?,
                    apify_run_id = ?,
                    apify_dataset_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (source_url, youtube_video_id, title, thumbnail_url, language, raw_text, translated_text, summary, apify_run_id, apify_dataset_id, source_id),
            )
            conn.commit()

    def delete_script_source(self, source_id: int, topic_id: int | None = None):
        with self._get_connection() as conn:
            if topic_id is None:
                conn.execute("DELETE FROM script_sources WHERE id = ?", (source_id,))
            else:
                conn.execute("DELETE FROM script_sources WHERE id = ? AND topic_id = ?", (source_id, topic_id))
            conn.commit()

    def list_script_sources(self, topic_id: int):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM script_sources WHERE topic_id = ? ORDER BY id DESC", (topic_id,))
            return [dict(row) for row in cursor.fetchall()]

    def add_script_draft(self, topic_id: int, content: str, draft_type: str = "outline", version: int = 1):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO script_drafts (topic_id, content, draft_type, version) VALUES (?, ?, ?, ?)",
                (topic_id, content, draft_type, version),
            )
            conn.commit()
            return cursor.lastrowid

    def list_script_drafts(self, topic_id: int):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM script_drafts WHERE topic_id = ? ORDER BY version DESC, id DESC", (topic_id,))
            return [dict(row) for row in cursor.fetchall()]

    def update_script_topic(self, topic_id: int, title: str, topic: str, status: str | None = None):
        updates = [
            "title = ?",
            "topic = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params = [title, topic]
        if status is not None:
            updates.insert(2, "status = ?")
            params.insert(2, status)
        params.append(topic_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE script_topics SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()

    def add_script_log(self, topic_id: int | None, event_type: str, message: str, details: dict | None = None, source_id: int | None = None):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO script_logs (topic_id, source_id, event_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                (
                    topic_id,
                    source_id,
                    event_type,
                    message,
                    self._safe_json_dumps(details or {}),
                ),
            )
            conn.commit()

    def list_script_logs(self, topic_id: int, limit: int = 50):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM script_logs WHERE topic_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (topic_id, limit),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["details"] = self._safe_json_loads(row.get("details_json"), default={}) or {}
            return rows

    def get_gallery(self, limit=25, offset=0, search=None, file_type=None, channel_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = """
                SELECT m.*, a.id as asset_id, a.prompt, a.niche, a.model, a.asset_tag, a.is_ai 
                FROM media m
                LEFT JOIN ai_assets a ON m.id = a.media_id
                WHERE 1=1
            """
            params = []
            if channel_id is not None:
                query += " AND m.channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (m.original_name LIKE ? OR a.prompt LIKE ? OR a.niche LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
            if file_type:
                query += " AND m.file_type = ?"
                params.append(file_type)
                
            query += " ORDER BY m.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_gallery(self, search=None, file_type=None, channel_id=None):
        with self._get_connection() as conn:
            query = "SELECT COUNT(*) FROM media m LEFT JOIN ai_assets a ON m.id = a.media_id WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND m.channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (m.original_name LIKE ? OR a.prompt LIKE ? OR a.niche LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
            if file_type:
                query += " AND m.file_type = ?"
                params.append(file_type)
            return conn.execute(query, params).fetchone()[0]

    # AI Assets Methods
    def tag_as_asset(self, media_id, prompt, niche, model="manual", asset_tag=None, is_ai=0):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_assets (media_id, prompt, niche, model, asset_tag, is_ai) VALUES (?, ?, ?, ?, ?, ?)",
                (media_id, prompt, niche, model, asset_tag, is_ai)
            )
            conn.commit()

    def find_assets(self, niche=None, prompt_query=None, limit=10):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT m.*, a.prompt, a.niche, a.asset_tag FROM media m JOIN ai_assets a ON m.id = a.media_id WHERE 1=1"
            params = []
            if niche:
                query += " AND a.niche = ?"
                params.append(niche)
            if prompt_query:
                query += " AND a.prompt LIKE ?"
                params.append(f"%{prompt_query}%")
            
            query += " ORDER BY RANDOM() LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def find_exact_asset(self, prompt, niche=None):
        """Busca un asset existente que coincida exactamente con el prompt para ahorrar créditos."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT m.*, a.prompt, a.niche FROM media m JOIN ai_assets a ON m.id = a.media_id WHERE a.prompt = ?"
            params = [prompt]
            if niche:
                query += " AND a.niche = ?"
                params.append(niche)
            
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_media(self, media_id):
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT file_path FROM media WHERE id = ?", (media_id,))
            row = cursor.fetchone()
            if row:
                path = row[0]
                if os.path.exists(path):
                    os.remove(path)
                conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
                conn.commit()
                return True
            return False

    # Templates Methods
    def get_templates(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM templates")
            return [dict(row) for row in cursor.fetchall()]

    def check_title_exists(self, title: str, exclude_job_id: str = None, channel_id: int | None = None) -> bool:
        """Returns True if a job with this exact title already exists (excluding a specific job_id if provided)."""
        if not title or not title.strip():
            return False
        with self._get_connection() as conn:
            query = "SELECT COUNT(*) FROM jobs WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))"
            params = [title]
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            
            if exclude_job_id:
                query += " AND job_id != ?"
                params.append(exclude_job_id)
                
            cursor = conn.execute(query, params)
            return cursor.fetchone()[0] > 0

    def add_job(self, job_id, text, niche, voice_id, status="processing", scenes_json=None, music_filename=None, music_volume=None, voice_volume=None, tts_engine=None, tts_speed=None, title=None, channel_id=None, intro_fade_duration=None, outro_fade_duration=None, music_fade_out_duration=None, tail_silence_seconds=None):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, channel_id, title, text, niche, voice_id, status, scenes_json, music_filename, music_volume, voice_volume, tts_engine, tts_speed, intro_fade_duration, outro_fade_duration, music_fade_out_duration, tail_silence_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, channel_id, title, text, niche, voice_id, status, scenes_json, music_filename, music_volume, voice_volume, tts_engine, tts_speed, intro_fade_duration, outro_fade_duration, music_fade_out_duration, tail_silence_seconds)
            )
            conn.commit()

    def save_or_update_job(self, job_id, text, niche, voice_id, status="processing", scenes_json=None, music_filename=None, music_volume=None, voice_volume=None, tts_engine=None, tts_speed=None, title=None, channel_id=None, intro_fade_duration=None, outro_fade_duration=None, music_fade_out_duration=None, tail_silence_seconds=None):
        with self._get_connection() as conn:
            existing = conn.execute("SELECT id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE jobs
                    SET channel_id = ?,
                        title = ?,
                        text = ?,
                        niche = ?,
                        voice_id = ?,
                        status = ?,
                        scenes_json = ?,
                        music_filename = ?,
                        music_volume = ?,
                        voice_volume = ?,
                        tts_engine = ?,
                        tts_speed = ?,
                        intro_fade_duration = ?,
                        outro_fade_duration = ?,
                        music_fade_out_duration = ?,
                        tail_silence_seconds = ?,
                        error_message = NULL,
                        finished_at = NULL
                    WHERE job_id = ?
                    """,
                    (channel_id, title, text, niche, voice_id, status, scenes_json, music_filename, music_volume, voice_volume, tts_engine, tts_speed, intro_fade_duration, outro_fade_duration, music_fade_out_duration, tail_silence_seconds, job_id)
                )
            else:
                conn.execute(
                    "INSERT INTO jobs (job_id, channel_id, title, text, niche, voice_id, status, scenes_json, music_filename, music_volume, voice_volume, tts_engine, tts_speed, intro_fade_duration, outro_fade_duration, music_fade_out_duration, tail_silence_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, channel_id, title, text, niche, voice_id, status, scenes_json, music_filename, music_volume, voice_volume, tts_engine, tts_speed, intro_fade_duration, outro_fade_duration, music_fade_out_duration, tail_silence_seconds)
                )
            conn.commit()

    def add_job_log(self, job_id: str, event_type: str, message: str, status: str = "info", details: dict | None = None,
                    channel_id: int | None = None, scene_id: str | None = None, actor: str = "system",
                    duration_ms: int | None = None, error_code: str | None = None, error_message: str | None = None):
        import json
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO job_logs (
                    job_id, channel_id, event_type, status, message, details_json,
                    scene_id, actor, duration_ms, error_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    channel_id,
                    event_type,
                    status,
                    message,
                    json.dumps(details, ensure_ascii=False) if details is not None else None,
                    scene_id,
                    actor,
                    duration_ms,
                    error_code,
                    error_message,
                )
            )
            conn.commit()

    def get_job_logs(self, job_id: str, limit: int = 100):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM job_logs
                WHERE job_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (job_id, limit),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["details"] = self._safe_json_loads(row.get("details_json"), default={}) or {}
            return rows

    def get_job(self, job_id: str):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_job_status(self, job_id, status, video_url=None, error_message=None):
        finished_at = datetime.now().isoformat() if status in ["completed", "failed"] else None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, video_url = ?, error_message = ?, finished_at = ? WHERE job_id = ?",
                (status, video_url, error_message, finished_at, job_id)
            )
            conn.commit()

    def mark_job_published(self, job_id: str, youtube_video_id: str, youtube_video_url: str):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE jobs SET youtube_video_id = ?, youtube_video_url = ?, youtube_published_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (youtube_video_id, youtube_video_url, job_id)
            )
            conn.commit()

    def clear_job_publication(self, job_id: str):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE jobs SET youtube_video_id = NULL, youtube_video_url = NULL, youtube_published_at = NULL WHERE job_id = ?",
                (job_id,)
            )
            conn.commit()

    def get_recent_jobs(self, limit=25, offset=0, search=None, channel_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM jobs WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (title LIKE ? OR text LIKE ? OR job_id LIKE ? OR niche LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
            
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_jobs(self, search=None, channel_id=None):
        with self._get_connection() as conn:
            query = "SELECT COUNT(*) FROM jobs WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (title LIKE ? OR text LIKE ? OR job_id LIKE ? OR niche LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
            return conn.execute(query, params).fetchone()[0]

    def update_ai_task(self, task_id, status, result_url=None, media_id=None, error_message=None):
        finished_at = datetime.now().isoformat() if status in ["completed", "failed"] else None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE ai_tasks SET status = ?, result_url = ?, media_id = ?, error_message = ?, finished_at = ? WHERE task_id = ?",
                (status, result_url, media_id, error_message, finished_at, task_id)
            )
            
            # Si pertenece a un lote, actualizar contadores del lote
            cursor = conn.execute("SELECT batch_id FROM ai_tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if row and row[0]:
                self._update_ai_batch_progress_internal(row[0], conn)
                
            conn.commit()

    def add_ai_task(self, task_id, prompt, niche, model, batch_id=None, channel_id=None):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO ai_tasks (task_id, channel_id, prompt, niche, model, status, batch_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, channel_id, prompt, niche, model, "processing", batch_id)
            )
            conn.commit()

    # AI Batches Methods
    def add_ai_batch(self, batch_id, total):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO ai_batches (batch_id, status, total_tasks) VALUES (?, ?, ?)",
                (batch_id, "processing", total)
            )
            conn.commit()

    def get_ai_batch(self, batch_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM ai_batches WHERE batch_id = ?", (batch_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_tasks_by_batch(self, batch_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM ai_tasks WHERE batch_id = ?", (batch_id,))
            return [dict(row) for row in cursor.fetchall()]

    def update_ai_batch_progress(self, batch_id):
        """Versión pública de actualización de progreso."""
        with self._get_connection() as conn:
            self._update_ai_batch_progress_internal(batch_id, conn)
            conn.commit()

    def _update_ai_batch_progress_internal(self, batch_id, conn):
        """Lógica interna de actualización de lote usando una conexión existente."""
        cursor = conn.execute("SELECT status FROM ai_tasks WHERE batch_id = ?", (batch_id,))
        statuses = [r[0] for r in cursor.fetchall()]
        
        if not statuses: return
        
        total = len(statuses)
        completed = statuses.count("completed")
        failed = statuses.count("failed")
        
        new_status = "processing"
        finished_at = None
        
        if completed + failed == total:
            finished_at = datetime.now().isoformat()
            if failed == 0:
                new_status = "completed"
            elif completed == 0:
                new_status = "failed"
            else:
                new_status = "partial"
        
        conn.execute(
            "UPDATE ai_batches SET status = ?, completed_tasks = ?, failed_tasks = ?, finished_at = ? WHERE batch_id = ?",
            (new_status, completed, failed, finished_at, batch_id)
        )


    def get_ai_tasks(self, limit=25, offset=0, search=None, channel_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM ai_tasks WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (prompt LIKE ? OR task_id LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])
            
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_ai_tasks(self, search=None, channel_id=None):
        with self._get_connection() as conn:
            query = "SELECT COUNT(*) FROM ai_tasks WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            if search:
                query += " AND (prompt LIKE ? OR task_id LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])
            return conn.execute(query, params).fetchone()[0]

    def delete_ai_task(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT media_id FROM ai_tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if row and row[0]:
                self.delete_media(row[0]) # Eliminamos el media asociado (archivo y DB media)
            
            result = conn.execute("DELETE FROM ai_tasks WHERE task_id = ?", (task_id,))
            conn.commit()
            return result.rowcount > 0

    def delete_job(self, job_id: str) -> bool:
        with self._get_connection() as conn:
            # Get video file path to delete it too
            cursor = conn.execute("SELECT video_url FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if row and row[0]:
                # video_url is like /static/shorts/xxx.mp4 → real path: storage/shorts/xxx.mp4
                video_path = row[0].replace("/static/", "storage/")
                if os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                    except Exception:
                        pass
            conn.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))
            result = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.commit()
            return result.rowcount > 0

    def get_stats(self, channel_id=None):
        with self._get_connection() as conn:
            if channel_id is None:
                total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                completed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'completed'").fetchone()[0]
                failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'").fetchone()[0]
            else:
                total = conn.execute("SELECT COUNT(*) FROM jobs WHERE channel_id = ?", (channel_id,)).fetchone()[0]
                completed = conn.execute("SELECT COUNT(*) FROM jobs WHERE channel_id = ? AND status = 'completed'", (channel_id,)).fetchone()[0]
                failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE channel_id = ? AND status = 'failed'", (channel_id,)).fetchone()[0]
            return {
                "total_jobs": total,
                "completed_jobs": completed,
                "failed_jobs": failed
            }

    def get_job_status_counts(self, channel_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS total FROM jobs WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            query += " GROUP BY COALESCE(status, 'unknown')"

            counts = {row["status"]: row["total"] for row in conn.execute(query, params).fetchall()}
            for key in ["draft", "processing", "rendered", "completed", "failed"]:
                counts.setdefault(key, 0)
            return counts

    def get_media_type_counts(self, channel_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT COALESCE(file_type, 'other') AS file_type, COUNT(*) AS total FROM media WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            query += " GROUP BY COALESCE(file_type, 'other')"

            counts = {row["file_type"]: row["total"] for row in conn.execute(query, params).fetchall()}
            for key in ["video", "image", "audio", "other"]:
                counts.setdefault(key, 0)
            return counts

    def get_recent_media(self, limit=4, channel_id=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = """
                SELECT m.*, a.id as asset_id, a.prompt, a.niche, a.model, a.asset_tag, a.is_ai
                FROM media m
                LEFT JOIN ai_assets a ON m.id = a.media_id
                WHERE 1=1
            """
            params = []
            if channel_id is not None:
                query += " AND m.channel_id = ?"
                params.append(channel_id)
            query += " ORDER BY m.created_at DESC LIMIT ?"
            params.append(limit)
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_job(self, channel_id=None, statuses=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM jobs WHERE 1=1"
            params = []
            if channel_id is not None:
                query += " AND channel_id = ?"
                params.append(channel_id)
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                query += f" AND COALESCE(status, 'unknown') IN ({placeholders})"
                params.extend(list(statuses))
            query += " ORDER BY created_at DESC LIMIT 1"
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def get_channel_overview(self, channel_id: int):
        channel = self.get_youtube_channel(channel_id)
        if not channel:
            return None

        stats = self.get_stats(channel_id=channel_id)
        job_counts = self.get_job_status_counts(channel_id=channel_id)
        media_counts = self.get_media_type_counts(channel_id=channel_id)
        recent_jobs = self.get_recent_jobs(limit=5, channel_id=channel_id)
        recent_media = self.get_recent_media(limit=4, channel_id=channel_id)
        latest_job = self.get_latest_job(channel_id=channel_id)
        latest_successful_job = self.get_latest_job(channel_id=channel_id, statuses=["rendered", "completed"])

        return {
            "channel": channel,
            "stats": stats,
            "job_counts": job_counts,
            "media_counts": media_counts,
            "recent_jobs": recent_jobs,
            "recent_media": recent_media,
            "latest_job": latest_job,
            "latest_successful_job": latest_successful_job,
        }

    # --- Candidates Management (Discovery) ---

    def add_candidates_batch(self, candidates: list):
        """
        Inserta un lote de candidatos. Ignora duplicados de título (UNIQUE constraint).
        """
        with self._get_connection() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO candidates (title, content, viral_score, source, niche, voice_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                (c.get('title'), c.get('content'), c.get('viral_score'), 
                 c.get('source'), c.get('niche'), c.get('voice_id'))
                for c in candidates
            ])
            conn.commit()

    def get_candidates(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM candidates ORDER BY viral_score DESC, created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_candidate_by_id(self, cand_id: int):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM candidates WHERE id = ?", (cand_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_candidate(self, cand_id: int) -> bool:
        with self._get_connection() as conn:
            result = conn.execute("DELETE FROM candidates WHERE id = ?", (cand_id,))
            conn.commit()
            return result.rowcount > 0

    def clear_all_candidates(self):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM candidates")
            conn.commit()

    def check_candidate_exists(self, title: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM candidates WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))", (title,))
            return cursor.fetchone()[0] > 0

    # --- YouTube Channels Management ---

    def list_youtube_channels(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM youtube_channels ORDER BY created_at DESC")
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["default_tags"] = self._safe_json_loads(row.get("default_tags"), default=[])
            return rows

    def get_youtube_channel(self, channel_id: int):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM youtube_channels WHERE id = ?", (channel_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["default_tags"] = self._safe_json_loads(data.get("default_tags"), default=[])
            return data

    def create_youtube_channel(self, payload: dict):
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO youtube_channels (
                    internal_name, internal_description, google_client_id, google_client_secret, google_redirect_uri,
                    default_privacy_status, default_category_id,
                    default_tags, default_language, notify_subscribers, status, connection_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                payload["internal_name"].strip(),
                payload.get("internal_description"),
                self._normalize_optional_text(payload.get("google_client_id")),
                self._normalize_optional_text(payload.get("google_client_secret")),
                self._normalize_optional_text(payload.get("google_redirect_uri")),
                payload.get("default_privacy_status", "private"),
                str(payload.get("default_category_id", "22")),
                self._safe_json_dumps(payload.get("default_tags", [])),
                payload.get("default_language", "es"),
                1 if payload.get("notify_subscribers") else 0,
                payload.get("status", "inactive"),
                payload.get("connection_status", "disconnected"),
            ))
            conn.commit()
            return cursor.lastrowid

    def update_youtube_channel(self, channel_id: int, payload: dict):
        fields = []
        values = []
        allowed = [
            "internal_name", "internal_description", "youtube_channel_id", "youtube_channel_title",
            "youtube_channel_handle", "youtube_channel_url", "thumbnail_url", "connected_google_email",
            "subscriber_count",
            "google_client_id", "google_client_secret", "google_redirect_uri",
            "default_privacy_status", "default_category_id", "default_tags", "default_language",
            "notify_subscribers", "status", "connection_status", "scopes_granted",
            "access_token_encrypted", "refresh_token_encrypted", "token_expires_at",
            "last_connection_test_at", "last_connection_error"
        ]

        for key in allowed:
            if key in payload:
                fields.append(f"{key} = ?")
                value = payload[key]
                if key == "notify_subscribers":
                    value = 1 if value else 0
                elif key == "default_tags":
                    value = self._safe_json_dumps(value or [])
                elif key in {"google_client_id", "google_client_secret", "google_redirect_uri"}:
                    value = self._normalize_optional_text(value)
                values.append(value)

        if not fields:
            return False

        values.append(channel_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE youtube_channels SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values
            )
            conn.commit()
            return True

    def delete_youtube_channel(self, channel_id: int) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM youtube_oauth_states WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM ai_tasks WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM jobs WHERE channel_id = ?", (channel_id,))
            conn.execute("DELETE FROM media WHERE channel_id = ?", (channel_id,))
            result = conn.execute("DELETE FROM youtube_channels WHERE id = ?", (channel_id,))
            conn.commit()
            return result.rowcount > 0

    def create_oauth_state(self, state: str, channel_id: int, expires_at, redirect_uri: str | None = None):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO youtube_oauth_states (state, channel_id, redirect_uri, expires_at) VALUES (?, ?, ?, ?)",
                (state, channel_id, redirect_uri, expires_at.isoformat())
            )
            conn.commit()

    def consume_oauth_state(self, state: str):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM youtube_oauth_states WHERE state = ? AND consumed_at IS NULL",
                (state,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            conn.execute(
                "UPDATE youtube_oauth_states SET consumed_at = CURRENT_TIMESTAMP WHERE state = ?",
                (state,)
            )
            conn.commit()
            return data

    @staticmethod
    def _safe_json_dumps(value):
        import json
        try:
            return json.dumps(value if value is not None else [])
        except Exception:
            return "[]"

    @staticmethod
    def _safe_json_loads(value, default=None):
        import json
        if default is None:
            default = []
        if value in (None, ""):
            return default
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_optional_text(value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None
