from __future__ import annotations

import base64
import hashlib
import sqlite3
import os
import uuid
import re
import logging
import time
import asyncio
import json
import pyotp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response, Depends, File, UploadFile, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, List, Optional, Union
from database import JobDatabase
from ai_manager import AIManager
from elevenlabs_manager import ElevenLabsManager
from video_editor import VideoEditor
from kie_manager import KieAiManager
from apify_manager import ApifyManager
from youtube_service import YouTubeChannelService, YouTubeAuthError

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

app = FastAPI(title="Shorts Generation Engine")
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# â”€â”€ Cache temporal de candidatos para selecciÃ³n Telegram â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Clave: session_id (8 chars), Valor: { expires_at, niche, voice_id, items[] }
_candidates_cache: dict = {}

# Security Configuration
DASHBOARD_PASSWORD = (os.getenv("DASHBOARD_PASSWORD") or "admin123").strip().strip('"').strip("'") or "admin123"
logger.info(f"Cargando configuraciÃ³n: DASHBOARD_PASSWORD detectada con longitud {len(DASHBOARD_PASSWORD)}")
TOTP_SECRET = (os.getenv("DASHBOARD_TOTP_SECRET") or "").strip()

if not TOTP_SECRET:
    # Stable fallback so 2FA survives restarts when the secret is not provided.
    digest = hashlib.sha256(DASHBOARD_PASSWORD.encode("utf-8")).digest()
    TOTP_SECRET = base64.b32encode(digest).decode("utf-8").rstrip("=")
    logger.warning("="*50)
    logger.warning("CONFIGURACIÃ“N DE SEGURIDAD 2FA (TOTP)")
    logger.warning("DASHBOARD_TOTP_SECRET no estaba configurado; se usarÃ¡ una clave derivada estable.")
    logger.warning("Escanea este cÃ³digo o introdÃºcelo en Authenticator:")
    logger.warning(pyotp.totp.TOTP(TOTP_SECRET).provisioning_uri(name="ShortsEngine", issuer_name="EstrellitaStudio"))
    logger.warning("="*50)

totp = pyotp.TOTP(TOTP_SECRET)
db = JobDatabase()
ai_manager = AIManager(db)

# Sessions (Simple In-Memory Store for demo, use Redis/DB for production if needed)
active_sessions = set()

# Load gTTS manager
tts_manager = ElevenLabsManager()
video_editor = VideoEditor()
kie_manager = KieAiManager(db)
apify_manager = ApifyManager(db)
youtube_manager = YouTubeChannelService(db)

# Directory for storage
BASE_DIR = "storage"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "audio"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "shorts"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "backgrounds"), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mounting static files
app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")
app.mount("/assets", StaticFiles(directory="static/dashboard"), name="assets")

@app.get("/icono.ico", include_in_schema=False)
async def site_favicon():
    return FileResponse(os.path.join(APP_ROOT, "static", "dashboard", "icono.ico"), media_type="image/x-icon")

# Authentication Helper
async def get_current_user(request: Request):
    # 1. Check Session Cookie (Existing)
    session_id = request.cookies.get("session_id")
    if session_id and session_id in active_sessions:
        return session_id
    
    # 2. Check X-API-Key Header (For n8n/automation)
    api_key = request.headers.get("X-API-Key")
    if api_key and api_key.strip() == DASHBOARD_PASSWORD:
        return "api_user"

    raise HTTPException(status_code=401, detail="No autenticado")

# Dashboard UI Routes
async def render_dashboard_file(request: Request, filename: str):
    try:
        await get_current_user(request)
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except HTTPException:
        return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    with open("static/dashboard/login.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/", response_class=HTMLResponse)
async def root_redirect(request: Request):
    return RedirectResponse(url="/channels")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/channels.html")

@app.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/channels.html")

@app.get("/overview", response_class=HTMLResponse)
async def overview_page(request: Request):
    return RedirectResponse(url="/channels")

@app.get("/channels/{channel_id}", response_class=HTMLResponse)
async def channel_workspace_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/channel-workspace.html")

@app.get("/channels/{channel_id}/history", response_class=HTMLResponse)
async def channel_history_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/channel-history.html")

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/jobs.html")

@app.get("/publish", response_class=HTMLResponse)
async def publish_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/publish.html")

@app.get("/gallery", response_class=HTMLResponse)
async def gallery_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/gallery.html")

@app.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/discover.html")

@app.get("/ai-tasks", response_class=HTMLResponse)
async def ai_tasks_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/ai-tasks.html")

@app.get("/storyboard", response_class=HTMLResponse)
async def storyboard_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/storyboard.html")

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/settings.html")

@app.get("/guiones", response_class=HTMLResponse)
async def scripts_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/guiones.html")

@app.get("/youtube-channels", response_class=HTMLResponse)
async def youtube_channels_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/youtube-channels.html")

# --- STORYBOARD MODELS ---
class StoryboardScene(BaseModel):
    text: Optional[str] = ""
    media_filename: Optional[str] = ""
    subtitle_pos: Optional[Union[int, str]] = 5
    subtitle_size: Optional[Union[int, str]] = 48
    show_text: Optional[bool] = True

class StoryboardRequest(BaseModel):
    scenes: List[StoryboardScene]
    music_filename: Optional[str] = None
    music_volume: Optional[float] = None
    voice_volume: Optional[float] = None
    voice_id: Optional[str] = None
    niche: str = "default"
    channel_id: Optional[int] = None
    job_id: Optional[str] = None
    tts_engine: Optional[str] = None
    tts_speed: Optional[float] = None
    title: Optional[str] = None  # TÃ­tulo Ãºnico del Short (para deduplicaciÃ³n)

class PublishVideoRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[Union[List[str], str]] = []
    privacy_status: Optional[str] = None
    category_id: Optional[str] = None
    channel_id: Optional[int] = None
    publish_at: Optional[str] = None
    license: Optional[str] = None
    embeddable: Optional[bool] = None
    public_stats_viewable: Optional[bool] = None
    made_for_kids: Optional[bool] = None
    contains_synthetic_media: Optional[bool] = None
    default_language: Optional[str] = None
    notify_subscribers: Optional[bool] = None

class UpdateYoutubeVideoRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[Union[List[str], str]] = []
    privacy_status: Optional[str] = None
    category_id: Optional[str] = None
    publish_at: Optional[str] = None
    license: Optional[str] = None
    embeddable: Optional[bool] = None
    public_stats_viewable: Optional[bool] = None
    made_for_kids: Optional[bool] = None
    contains_synthetic_media: Optional[bool] = None
    default_language: Optional[str] = None

class RelinkYoutubeVideoRequest(BaseModel):
    video_reference: str
    channel_id: Optional[int] = None

class ScriptTopicCreateRequest(BaseModel):
    channel_id: int
    title: str
    topic: str
    status: str = "draft"

class ScriptSourceCreateRequest(BaseModel):
    source_url: Optional[str] = None
    youtube_video_id: Optional[str] = None
    source_type: str = "youtube"
    language: Optional[str] = None
    raw_text: Optional[str] = None
    translated_text: Optional[str] = None
    summary: Optional[str] = None
    apify_run_id: Optional[str] = None
    apify_dataset_id: Optional[str] = None

class ScriptDraftCreateRequest(BaseModel):
    content: str
    draft_type: str = "outline"
    version: Optional[int] = 1

class CommentReplyDraftRequest(BaseModel):
    comment_text: str
    video_title: Optional[str] = None
    author_name: Optional[str] = None
    provider: Optional[str] = None

class CommentReplyPublishRequest(BaseModel):
    reply_text: str

def resolve_job_video_path(job: dict) -> str | None:
    video_url = (job or {}).get("video_url") or ""
    if not video_url:
        return None
    if video_url.startswith("/static/shorts/"):
        return os.path.join(BASE_DIR, "shorts", os.path.basename(video_url))
    if video_url.startswith("/static/"):
        return os.path.join(BASE_DIR, video_url.lstrip("/"))
    if os.path.isabs(video_url) and os.path.exists(video_url):
        return video_url
    candidate = os.path.join(BASE_DIR, video_url.lstrip("/"))
    return candidate if os.path.exists(candidate) else None

def parse_iso_datetime(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        raise HTTPException(status_code=400, detail="publish_at invÃ¡lido. Usa un ISO 8601 vÃ¡lido.")

def extract_youtube_video_id(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if "youtu.be" in host:
        candidate = path.strip("/").split("/")[0]
        if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        query_id = parse_qs(parsed.query).get("v", [None])[0]
        if query_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", query_id):
            return query_id

        parts = [segment for segment in path.split("/") if segment]
        for idx, segment in enumerate(parts):
            if segment in {"shorts", "embed", "live"} and idx + 1 < len(parts):
                candidate = parts[idx + 1]
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                    return candidate

    return None

def log_job_event(
    job_id: str,
    event_type: str,
    message: str,
    status: str = "info",
    details: dict | None = None,
    channel_id: int | None = None,
    scene_id: str | None = None,
    actor: str = "system",
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
):
    try:
        db.add_job_log(
            job_id=job_id,
            event_type=event_type,
            message=message,
            status=status,
            details=details,
            channel_id=channel_id,
            scene_id=scene_id,
            actor=actor,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )
    except Exception as exc:
        logger.debug(f"No se pudo guardar el log del trabajo {job_id}: {exc}")

class LoginRequest(BaseModel):
    password: str

class Verify2FARequest(BaseModel):
    temp_token: str
    code: str

class AiGenerateRequest(BaseModel):
    prompt: str
    niche: str = "general"
    model: Optional[str] = None
    channel_id: Optional[int] = None

class AiScenePrompt(BaseModel):
    prompt: str
    niche: str = "general"
    model: Optional[str] = None

class AiBatchGenerateRequest(BaseModel):
    scenes: List[AiScenePrompt]
    draft_mode: bool = False

class AIAssetTagRequest(BaseModel):
    media_id: int
    prompt: str
    niche: str
    asset_tag: Optional[str] = None
    is_ai: Optional[int] = 0

# Settings Models
class SettingSetRequest(BaseModel):
    provider: str
    api_key: str

class YouTubeChannelCreateRequest(BaseModel):
    internal_name: str
    internal_description: Optional[str] = None
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_uri: Optional[str] = None
    default_privacy_status: str = "private"
    default_category_id: str = "22"
    default_tags: Optional[Union[List[str], str]] = []
    default_language: str = "es"
    notify_subscribers: bool = False
    status: str = "inactive"

class YouTubeChannelUpdateRequest(BaseModel):
    internal_name: str
    internal_description: Optional[str] = None
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_uri: Optional[str] = None
    default_privacy_status: str = "private"
    default_category_id: str = "22"
    default_tags: Optional[Union[List[str], str]] = []
    default_language: str = "es"
    notify_subscribers: bool = False
    status: str = "inactive"

@app.post("/api/settings")
async def update_settings(req: SettingSetRequest, session=Depends(get_current_user)):
    db.set_setting(req.provider, req.api_key.strip())
    return {"status": "ok"}

@app.get("/api/settings")
async def get_settings(session=Depends(get_current_user)):
    """Devuelve las claves ofuscadas, la de 2FA y la de n8n."""
    keys = {}
    
    # Proveedores API
    for prov in ["GROQ", "OPENAI", "DEEPSEEK", "OPENROUTER",
                 "KIE_API_KEY_1", "KIE_API_KEY_2", "KIE_API_KEY_3", "KIE_API_KEY_4", "KIE_API_KEY_5",
                 "APIFY_API_KEY_1", "APIFY_API_KEY_2", "APIFY_API_KEY_3", "APIFY_API_KEY_4"]:
        val = db.get_setting(prov)
        keys[prov] = "********" if val else None

    # Ajustes varios
    keys["2FA_ENABLED"] = db.get_setting("2FA_ENABLED", "true")
    keys["KIE_CURRENT_KEY_INDEX"] = db.get_setting("KIE_CURRENT_KEY_INDEX", "1")
    keys["APIFY_CURRENT_KEY_INDEX"] = db.get_setting("APIFY_CURRENT_KEY_INDEX", "1")
    keys["DEFAULT_MUSIC_VOLUME"] = db.get_setting("DEFAULT_MUSIC_VOLUME")
    keys["DEFAULT_VOICE_VOLUME"] = db.get_setting("DEFAULT_VOICE_VOLUME")

    return keys

@app.get("/api/youtube/channels")
async def api_list_youtube_channels(user: str = Depends(get_current_user)):
    raw_channels = db.list_youtube_channels()
    channels = []
    for ch in raw_channels:
        if (
            ch.get("connection_status") == "connected"
            and (
                ch.get("subscriber_count") is None
                or ch.get("view_count") is None
                or ch.get("video_count") is None
            )
            and ch.get("access_token_encrypted")
        ):
            try:
                ch = youtube_manager.refresh_channel_snapshot(int(ch["id"]))
            except Exception:
                ch = db.get_youtube_channel(int(ch["id"]))
        safe_channel = serialize_youtube_channel(ch)
        if safe_channel:
            safe_channel["jobs_count"] = db.count_jobs(channel_id=int(safe_channel["id"]))
        channels.append(safe_channel)
    return {"items": channels}

@app.post("/api/youtube/channels")
async def api_create_youtube_channel(req: YouTubeChannelCreateRequest, user: str = Depends(get_current_user)):
    if not req.internal_name or not req.internal_name.strip():
        raise HTTPException(status_code=400, detail="El nombre interno es obligatorio.")

    privacy = req.default_privacy_status.strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="default_privacy_status invÃ¡lido.")

    channel_id = db.create_youtube_channel({
        "internal_name": req.internal_name,
        "internal_description": req.internal_description,
        "google_client_id": req.google_client_id,
        "google_client_secret": req.google_client_secret,
        "google_redirect_uri": req.google_redirect_uri,
        "default_privacy_status": privacy,
        "default_category_id": str(req.default_category_id or "22"),
        "default_tags": normalize_tags_input(req.default_tags),
        "default_language": (req.default_language or "es").strip() or "es",
        "notify_subscribers": bool(req.notify_subscribers),
        "status": req.status if req.status in {"active", "inactive"} else "inactive",
        "connection_status": "disconnected",
    })
    channel = db.get_youtube_channel(channel_id)
    return {"status": "success", "channel": serialize_youtube_channel(channel)}

@app.get("/api/youtube/channels/{channel_id}")
async def api_get_youtube_channel(channel_id: int, user: str = Depends(get_current_user)):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    return serialize_youtube_channel(channel)

@app.get("/api/youtube/channels/{channel_id}/overview")
async def api_get_youtube_channel_overview(channel_id: int, user: str = Depends(get_current_user)):
    overview = db.get_channel_overview(channel_id)
    if not overview:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    comment_videos = []
    try:
        recent_published_jobs = [
            job for job in db.get_recent_jobs(limit=10, channel_id=channel_id)
            if job.get("youtube_video_id")
        ]
        video_ids = [str(job["youtube_video_id"]).strip() for job in recent_published_jobs if str(job.get("youtube_video_id") or "").strip()]
        stats_map = youtube_manager.get_video_statistics(channel_id, video_ids) if video_ids else {}
        for job in recent_published_jobs:
            video_id = str(job.get("youtube_video_id") or "").strip()
            if not video_id:
                continue
            stats = stats_map.get(video_id, {})
            comment_count = int(stats.get("comment_count") or 0)
            if comment_count <= 0:
                continue
            comment_videos.append({
                "job_id": job.get("job_id"),
                "title": job.get("title") or job.get("text") or job.get("job_id"),
                "video_id": video_id,
                "video_url": job.get("youtube_video_url") or f"https://www.youtube.com/watch?v={video_id}",
                "comment_count": comment_count,
                "created_at": job.get("created_at"),
            })
    except Exception as exc:
        logger.debug(f"No se pudieron cargar vÃ­deos con comentarios para canal {channel_id}: {exc}")

    return {
        "channel": serialize_youtube_channel(overview["channel"]),
        "stats": overview["stats"],
        "job_counts": overview["job_counts"],
        "media_counts": overview["media_counts"],
        "recent_jobs": overview["recent_jobs"],
        "recent_media": overview["recent_media"],
        "latest_job": overview["latest_job"],
        "latest_successful_job": overview["latest_successful_job"],
        "comment_videos": comment_videos,
    }

@app.put("/api/youtube/channels/{channel_id}")
async def api_update_youtube_channel(channel_id: int, req: YouTubeChannelUpdateRequest, user: str = Depends(get_current_user)):
    existing = db.get_youtube_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    privacy = req.default_privacy_status.strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="default_privacy_status invÃ¡lido.")

    db.update_youtube_channel(channel_id, {
        "internal_name": req.internal_name,
        "internal_description": req.internal_description,
        "google_client_id": req.google_client_id if req.google_client_id is not None and req.google_client_id.strip() else existing.get("google_client_id"),
        "google_client_secret": req.google_client_secret if req.google_client_secret is not None and req.google_client_secret.strip() else existing.get("google_client_secret"),
        "google_redirect_uri": req.google_redirect_uri if req.google_redirect_uri is not None and req.google_redirect_uri.strip() else existing.get("google_redirect_uri"),
        "default_privacy_status": privacy,
        "default_category_id": str(req.default_category_id or "22"),
        "default_tags": normalize_tags_input(req.default_tags),
        "default_language": (req.default_language or "es").strip() or "es",
        "notify_subscribers": bool(req.notify_subscribers),
        "status": req.status if req.status in {"active", "inactive"} else "inactive",
    })
    return {"status": "success", "channel": serialize_youtube_channel(db.get_youtube_channel(channel_id))}

@app.delete("/api/youtube/channels/{channel_id}")
async def api_delete_youtube_channel(channel_id: int, user: str = Depends(get_current_user)):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    db.delete_youtube_channel(channel_id)
    return {"status": "success"}

@app.get("/api/youtube/channels/{channel_id}/connect")
async def api_connect_youtube_channel(channel_id: int, request: Request, user: str = Depends(get_current_user)):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    callback_url = str(request.url_for("api_youtube_oauth_callback"))
    auth = youtube_manager.generate_auth_url(channel_id, redirect_uri=callback_url)
    return RedirectResponse(url=auth["auth_url"])

@app.get("/api/youtube/oauth/callback")
async def api_youtube_oauth_callback(code: str = None, state: str = None, error: str = None):
    if error:
        logger.warning(f"OAuth de YouTube cancelado o fallido: {error}")
        return RedirectResponse(url=f"/youtube-channels?oauth=error&message={error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan parÃ¡metros OAuth.")

    try:
        channel = youtube_manager.handle_oauth_callback(code, state)
        if channel:
            return RedirectResponse(url=f"/youtube-channels?oauth=success&id={channel['id']}")
        return RedirectResponse(url="/youtube-channels?oauth=success")
    except YouTubeAuthError as exc:
        logger.error(f"Callback OAuth fallÃ³: {exc}")
        return RedirectResponse(url=f"/youtube-channels?oauth=error&message={str(exc)}")
    except Exception as exc:
        logger.error(f"Error inesperado OAuth callback: {exc}")
        return RedirectResponse(url="/youtube-channels?oauth=error&message=Error inesperado al conectar")

@app.post("/api/youtube/channels/{channel_id}/test")
async def api_test_youtube_channel(channel_id: int, user: str = Depends(get_current_user)):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    result = youtube_manager.test_connection(channel_id)
    return result

@app.post("/api/youtube/channels/{channel_id}/revoke")
async def api_revoke_youtube_channel(channel_id: int, user: str = Depends(get_current_user)):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    try:
        result = youtube_manager.revoke_connection(channel_id)
        return result
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/apify/accounts")
async def api_get_apify_accounts(user: str = Depends(get_current_user)):
    return {"accounts": apify_manager.get_accounts_status()}

class ScriptApifyImportRequest(BaseModel):
    actor_id: Optional[str] = None
    input_payload: dict[str, Any] = {}

@app.get("/api/scripts/topics")
async def api_list_script_topics(channel_id: int = None, search: str = None, limit: int = 50, offset: int = 0, user: str = Depends(get_current_user)):
    return {
        "items": db.list_script_topics(channel_id=channel_id, search=search, limit=limit, offset=offset),
        "total": len(db.list_script_topics(channel_id=channel_id, search=search, limit=1000, offset=0)),
    }

@app.post("/api/scripts/topics")
async def api_create_script_topic(req: ScriptTopicCreateRequest, user: str = Depends(get_current_user)):
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="El tÃ­tulo del guion es obligatorio.")
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="El tema del guion es obligatorio.")
    status = req.status if req.status in {"draft", "active", "archived"} else "draft"
    topic_id = db.create_script_topic(req.channel_id, req.title.strip(), req.topic.strip(), status=status)
    db.add_script_log(topic_id, "created", "Tema de guion creado correctamente.", {"title": req.title.strip(), "topic": req.topic.strip()})
    return {"status": "success", "topic": db.get_script_topic(topic_id)}

@app.get("/api/scripts/topics/{topic_id}")
async def api_get_script_topic(topic_id: int, user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")
    return {
        "topic": topic,
        "sources": db.list_script_sources(topic_id),
        "drafts": db.list_script_drafts(topic_id),
        "logs": db.list_script_logs(topic_id, limit=50),
    }

@app.put("/api/scripts/topics/{topic_id}")
async def api_update_script_topic(topic_id: int, req: ScriptTopicCreateRequest, user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")
    status = req.status if req.status in {"draft", "active", "archived"} else "draft"
    db.update_script_topic(topic_id, req.title.strip(), req.topic.strip(), status=status)
    db.add_script_log(topic_id, "edited", "Tema de guion actualizado.", {"title": req.title.strip(), "topic": req.topic.strip(), "status": status})
    return {"status": "success", "topic": db.get_script_topic(topic_id)}

@app.post("/api/scripts/topics/{topic_id}/sources")
async def api_add_script_source(topic_id: int, req: ScriptSourceCreateRequest, user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")
    source_url = (req.source_url or "").strip() or None
    youtube_video_id = (req.youtube_video_id or "").strip() or extract_youtube_video_id(source_url)
    raw_text = (req.raw_text or "").strip() or None
    language = req.language
    translated_text = req.translated_text
    summary = req.summary
    apify_run_id = req.apify_run_id
    apify_dataset_id = req.apify_dataset_id
    source_title = None
    thumbnail_url = None

    existing_source = db.find_script_source(topic_id, source_url=source_url, youtube_video_id=youtube_video_id)
    if existing_source and existing_source.get("raw_text") and existing_source.get("summary"):
        db.add_script_log(
            topic_id,
            "source_reused",
            "Se reutilizó una fuente ya transcrita y resumida para este tema.",
            {"source_url": existing_source.get("source_url"), "youtube_video_id": existing_source.get("youtube_video_id")},
            source_id=existing_source.get("id"),
        )
        return {"status": "success", "source": existing_source, "reused": True}

    if source_url and not raw_text:
        try:
            scraped_item = apify_manager.fetch_youtube_transcript(source_url)
            normalized = normalize_apify_source_item(scraped_item, fallback_url=source_url)
            source_url = normalized["source_url"] or source_url
            youtube_video_id = normalized["youtube_video_id"] or youtube_video_id
            source_title = normalized["title"] or source_title
            thumbnail_url = normalized["thumbnail_url"] or thumbnail_url
            raw_text = normalized["raw_text"] or raw_text
            language = normalized["language"] or language
            translated_text = normalized["translated_text"] or translated_text
            if raw_text:
                db.add_script_log(
                    topic_id,
                    "source_transcribed",
                    "Transcripción obtenida desde YouTube con Apify.",
                    {
                        "source_url": source_url,
                        "youtube_video_id": youtube_video_id,
                        "language": language,
                        "has_text": True,
                    },
                )
        except Exception as exc:
            logger.warning("No se pudo extraer la transcripción desde Apify para %s: %s", source_url, exc)

    if raw_text and not summary:
        ai_result = summarize_source_in_spanish(
            raw_text,
            title=source_title or topic.get("title"),
            source_language=language,
        )
        summary = ai_result.get("summary") or summary
        translated_text = translated_text or ai_result.get("translated_text")
        db.add_script_log(
            topic_id,
            "source_summarized",
            "Resumen en español generado a partir de la transcripción.",
            {
                "source_url": source_url,
                "youtube_video_id": youtube_video_id,
                "language": language,
                "has_summary": bool(summary),
            },
        )

    if source_url and not raw_text and not summary:
        raise HTTPException(
            status_code=400,
            detail="No se pudo obtener la transcripción de ese vídeo. Prueba con otra URL o pega la transcripción manualmente.",
        )
    if existing_source:
        db.update_script_source(
            existing_source["id"],
            source_url=source_url or existing_source.get("source_url"),
            youtube_video_id=youtube_video_id or existing_source.get("youtube_video_id"),
            title=source_title or existing_source.get("title"),
            thumbnail_url=thumbnail_url or existing_source.get("thumbnail_url"),
            language=language or existing_source.get("language"),
            raw_text=raw_text or existing_source.get("raw_text"),
            translated_text=translated_text or existing_source.get("translated_text"),
            summary=summary or existing_source.get("summary"),
            apify_run_id=apify_run_id or existing_source.get("apify_run_id"),
            apify_dataset_id=apify_dataset_id or existing_source.get("apify_dataset_id"),
        )
        source_id = existing_source["id"]
        db.add_script_log(
            topic_id,
            "source_updated",
            "Fuente del tema actualizada con transcripción y resumen.",
            {"source_url": source_url, "youtube_video_id": youtube_video_id, "has_text": bool(raw_text), "has_summary": bool(summary)},
            source_id=source_id,
        )
    else:
        source_id = db.add_script_source(
            topic_id=topic_id,
            source_url=source_url,
            youtube_video_id=youtube_video_id,
            title=source_title,
            thumbnail_url=thumbnail_url,
            source_type=req.source_type or "youtube",
            language=language,
            raw_text=raw_text,
            translated_text=translated_text,
            summary=summary,
            apify_run_id=apify_run_id,
            apify_dataset_id=apify_dataset_id,
            channel_id=topic.get("channel_id"),
        )
        db.add_script_log(
            topic_id,
            "source_added",
            "Vídeo añadido al tema con su transcripción y resumen.",
            {"source_url": source_url, "youtube_video_id": youtube_video_id, "has_text": bool(raw_text), "has_summary": bool(summary)},
            source_id=source_id,
        )

    source = db.find_script_source(topic_id, source_url=source_url, youtube_video_id=youtube_video_id)
    return {"status": "success", "source": source}

@app.delete("/api/scripts/sources/{source_id}")
async def api_delete_script_source(source_id: int, topic_id: int = Query(...), user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")
    with db._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM script_sources WHERE id = ? AND topic_id = ?",
            (source_id, topic_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")

    db.delete_script_source(source_id, topic_id=topic_id)
    db.add_script_log(
        topic_id,
        "source_deleted",
        "Vídeo eliminado de las fuentes del tema.",
        {
            "source_id": source_id,
            "source_url": dict(row).get("source_url"),
            "youtube_video_id": dict(row).get("youtube_video_id"),
        },
        source_id=source_id,
    )
    return {"status": "success"}

@app.post("/api/scripts/topics/{topic_id}/drafts")
async def api_add_script_draft(topic_id: int, req: ScriptDraftCreateRequest, user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")
    draft_id = db.add_script_draft(topic_id=topic_id, content=req.content, draft_type=req.draft_type, version=req.version or 1)
    db.add_script_log(topic_id, "draft_saved", "Borrador de guion guardado.", {"draft_type": req.draft_type, "version": req.version or 1})
    return {"status": "success", "draft_id": draft_id}

@app.post("/api/scripts/topics/{topic_id}/apify-import")
async def api_apify_import_topic_sources(topic_id: int, req: ScriptApifyImportRequest, user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")
    if not req.input_payload:
        raise HTTPException(status_code=400, detail="El payload de Apify no puede estar vac?o.")
    try:
        result = apify_manager.run_youtube_scraper(req.input_payload, actor_id=req.actor_id)
        imported = 0
        items = result if isinstance(result, list) else (result.get("items") or result.get("data") or [])
        for item in items:
            normalized = normalize_apify_source_item(item)
            if not (normalized["source_url"] or normalized["youtube_video_id"] or normalized["raw_text"]):
                continue
            db.add_script_source(
                topic_id=topic_id,
                source_url=normalized["source_url"],
                youtube_video_id=normalized["youtube_video_id"],
                title=normalized["title"],
                thumbnail_url=normalized["thumbnail_url"],
                source_type="youtube",
                language=normalized["language"],
                raw_text=normalized["raw_text"],
                translated_text=normalized["translated_text"],
                summary=normalized["summary"],
                channel_id=topic.get("channel_id"),
            )
            imported += 1
        db.add_script_log(
            topic_id,
            "apify_import",
            f"Importadas {imported} fuentes desde Apify.",
            {"actor_id": req.actor_id or apify_manager.default_youtube_actor, "imported": imported},
        )
        return {"status": "success", "imported": imported, "result": result}
    except Exception as exc:
        db.add_script_log(topic_id, "apify_import_failed", "Fall? la importaci?n con Apify.", {"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/auth/login")
async def api_login(req: LoginRequest, response: Response):
    attempt = req.password.strip().strip('"').strip("'")
    if attempt == DASHBOARD_PASSWORD:
        # Verificar si 2FA estÃ¡ activo
        is_2fa_enabled = db.get_setting("2FA_ENABLED") != "false"
        
        if is_2fa_enabled:
            temp_token = str(uuid.uuid4())
            return {"temp_token": temp_token}
        else:
            # Login directo
            session_id = str(uuid.uuid4())
            active_sessions.add(session_id)
            response.set_cookie(key="session_id", value=session_id, httponly=True)
            return {"status": "success"}
            
    raise HTTPException(status_code=401, detail="ContraseÃ±a incorrecta")

@app.post("/api/auth/verify-2fa")
async def api_verify_2fa(req: Verify2FARequest, response: Response):
    if totp.verify(req.code):
        session_id = str(uuid.uuid4())
        active_sessions.add(session_id)
        response.set_cookie(key="session_id", value=session_id, httponly=True)
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="CÃ³digo 2FA invÃ¡lido")

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Error de validaciÃ³n 422: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )

def normalize_tags_input(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]
    text = str(value).strip()
    return [text] if text else []

def serialize_youtube_channel(channel: dict | None) -> dict | None:
    if not channel:
        return None
    safe = dict(channel)
    safe.pop("access_token_encrypted", None)
    safe.pop("refresh_token_encrypted", None)
    if safe.get("google_client_secret"):
        safe["google_client_secret"] = "********"
    safe["default_tags"] = normalize_tags_input(safe.get("default_tags"))
    return safe


def flatten_transcript_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    parts.append(cleaned)
            elif isinstance(item, dict):
                cleaned = str(item.get("text") or item.get("subtitle") or item.get("caption") or "").strip()
                if cleaned:
                    parts.append(cleaned)
        joined = " ".join(parts).strip()
        return joined or None
    return str(value).strip() or None


def summarize_source_in_spanish(raw_text: str | None, title: str | None = None, source_language: str | None = None) -> dict[str, str]:
    text = (raw_text or "").strip()
    if not text:
        return {"summary": "", "translated_text": ""}
    try:
        return ai_manager.summarize_script_source(text, title=title, source_language=source_language)
    except Exception as exc:
        logger.warning("No se pudo resumir la fuente con IA: %s", exc)
        fallback = text[:1200].strip()
        return {"summary": fallback, "translated_text": ""}

def normalize_apify_source_item(item: dict, fallback_url: str | None = None) -> dict:
    if not isinstance(item, dict):
        item = {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    source_url = (
        item.get("url")
        or item.get("videoUrl")
        or item.get("video_url")
        or item.get("sourceUrl")
        or metadata.get("url")
        or fallback_url
    )
    youtube_video_id = (
        item.get("videoId")
        or item.get("video_id")
        or item.get("id")
        or metadata.get("video_id")
        or metadata.get("videoId")
        or extract_youtube_video_id(source_url)
    )
    title = item.get("videoTitle") or item.get("title") or item.get("name") or metadata.get("title") or ""
    thumbnail_url = (
        item.get("thumbnailUrl")
        or item.get("thumbnail_url")
        or item.get("thumbnail")
        or item.get("thumbnail_image_url")
        or metadata.get("thumbnail")
        or metadata.get("thumbnail_url")
    )
    raw_text = flatten_transcript_text(
        item.get("transcript")
        or item.get("text")
        or item.get("content")
        or item.get("caption")
        or item.get("subtitles")
        or item.get("translation")
        or metadata.get("transcript")
        or metadata.get("translation")
    )
    language = (
        item.get("activeLanguageCode")
        or item.get("language")
        or item.get("lang")
        or item.get("transcriptLanguage")
        or item.get("subtitlesLanguage")
        or metadata.get("source_caption_language_code")
        or metadata.get("target_language")
    )
    summary = item.get("summary") or item.get("shortDescription") or ""
    translated_text = item.get("translated_text") or ""
    translation = item.get("translation")
    if isinstance(translation, dict):
        translated_text = translation.get("text") or translated_text
    elif isinstance(translation, str):
        translated_text = translation or translated_text
    if raw_text and not summary:
        ai_result = summarize_source_in_spanish(raw_text, title=title, source_language=language)
        summary = ai_result.get("summary", "") or title or ""
        if not translated_text:
            translated_text = ai_result.get("translated_text", "") or ""
    return {
        "source_url": source_url,
        "youtube_video_id": youtube_video_id,
        "title": title,
        "thumbnail_url": thumbnail_url,
        "language": language,
        "raw_text": raw_text,
        "translated_text": translated_text,
        "summary": summary,
    }

# Dashboard API Data
@app.get("/api/jobs/check-title")
async def api_check_title(title: str, exclude: str = None, channel_id: int = None, user: str = Depends(get_current_user)):
    """
    Verifica si ya existe un job con este tÃ­tulo exacto.
    Usado por n8n ANTES de crear un nuevo Short para evitar duplicados y ahorrar crÃ©ditos.
    Responde con {"exists": true/false, "title": "..."}.
    Si exists=true, n8n debe buscar una historia diferente.
    """
    exists = db.check_title_exists(title, exclude_job_id=exclude, channel_id=channel_id)
    return {
        "exists": exists,
        "title": title,
        "message": "El tÃ­tulo ya existe. Busca otra historia viral." if exists else "TÃ­tulo disponible. Puedes crear el Short."
    }

class PostItem(BaseModel):
    title: str
    content: str
    score: int = 0
    upvote_ratio: float = 0.0
    num_comments: int = 0
    viral_score: int = 0
    source: Optional[str] = "unknown"
    niche: Optional[str] = "mixed"
    voice_id: Optional[str] = ""

class CandidatesBatchRequest(BaseModel):
    items: List[PostItem]

@app.get("/api/candidates")
async def api_get_candidates(user: str = Depends(get_current_user)):
    return db.get_candidates()

@app.post("/api/candidates/batch")
async def api_add_candidates_batch(data: CandidatesBatchRequest, user: str = Depends(get_current_user)):
    db.add_candidates_batch([item.dict() for item in data.items])
    return {"status": "ok", "count": len(data.items)}

@app.delete("/api/candidates/{cand_id}")
async def api_delete_candidate(cand_id: Union[int, str], user: str = Depends(get_current_user)):
    if cand_id == "all":
        db.clear_all_candidates()
        return {"status": "ok", "message": "Todos los candidatos han sido eliminados."}
    
    try:
        cand_id_int = int(cand_id)
        success = db.delete_candidate(cand_id_int)
        if not success:
            raise HTTPException(status_code=404, detail="Candidato no encontrado")
        return {"status": "ok"}
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de candidato invÃ¡lido")

@app.post("/api/candidates/{cand_id}/process")
async def api_process_candidate(cand_id: int, request: Request, background_tasks: BackgroundTasks):
    """
    Toma un candidato, genera un storyboard con IA (traducciÃ³n, escenas, prompts),
    reutiliza assets de galerÃ­a si existen, y crea un borrador en el engine.
    """
    await get_current_user(request)
    candidate = db.get_candidate_by_id(cand_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidato no encontrado")

    try:
        # 1. Generar Storyboard IA (Simula lo que hacÃ­a n8n)
        logger.info(f"Generando storyboard IA para candidato {cand_id}: {candidate['title']}")
        storyboard_data = ai_manager.generate_storyboard(candidate["content"])
        scenes_raw = storyboard_data.get("scenes", [])
        
        if not scenes_raw:
            # Fallback si la IA falla o el JSON es vacÃ­o
            scenes_raw = [{"text": candidate["content"][:500], "image_prompt": "cinematic background", "subtitle_pos": 8, "subtitle_size": 48}]

        # 2. Gallery-First & Preparar escenas finales
        final_scenes = []
        batch_scenes_to_gen = []
        
        for scene in scenes_raw:
            prompt = scene.get("image_prompt", "")
            niche = candidate.get("niche", "default")
            
            # Buscar coincidencia exacta en galerÃ­a
            existing_asset = db.find_exact_asset(prompt, niche)
            
            new_scene = {
                "text": scene.get("text", ""),
                "subtitle_pos": scene.get("subtitle_pos", 8),
                "subtitle_size": scene.get("subtitle_size", 48),
                "media_filename": "NICHE"
            }
            
            if existing_asset:
                new_scene["media_filename"] = existing_asset["filename"]
                logger.info(f"Reutilizando asset: {existing_asset['filename']}")
            else:
                # Marcar para generar
                batch_scenes_to_gen.append(AiScenePrompt(
                    prompt=prompt,
                    niche=niche,
                    model="seedream/5-lite-text-to-image"
                ))
            
            final_scenes.append(new_scene)

        # 3. Crear el Job en la DB
        job_id = f"cinema_{str(uuid.uuid4())[:8]}"
        import json
        db.add_job(
            job_id,
            f"IA: {candidate['title']}",
            candidate["niche"],
            candidate["voice_id"],
            status="draft",
            scenes_json=json.dumps(final_scenes),
            title=candidate["title"]
        )
        
        # 4. Crear tareas de IA en modo BORRADOR (Human-in-the-Loop)
        if batch_scenes_to_gen:
            batch_id = f"batch_{str(uuid.uuid4())[:8]}"
            db.add_ai_batch(batch_id, len(batch_scenes_to_gen))
            
            for scene in batch_scenes_to_gen:
                # Generamos un ID de tarea local para el borrador
                task_id = f"draft_{str(uuid.uuid4())[:8]}"
                # Registramos en la BD como borrador
                db.add_ai_task(task_id, scene.prompt, scene.niche, scene.model, batch_id=batch_id)
                db.update_ai_task(task_id, "draft")
                
            logger.info(f"Creadas {len(batch_scenes_to_gen)} tareas de IA en modo borrador para el lote {batch_id}")

        # 5. Borrar del descubrimiento
        db.delete_candidate(cand_id)
        
        return {
            "status": "ok", 
            "job_id": job_id, 
            "message": "âœ… Storyboard e imÃ¡genes (borrador) listos en el engine."
        }
        
    except Exception as e:
        logger.error(f"Error en api_process_candidate: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en la generaciÃ³n IA: {str(e)}")

@app.get("/api/jobs")
async def api_get_jobs(page: int = 1, limit: int = 25, search: str = None, channel_id: int = None, user: str = Depends(get_current_user)):
    offset = (page - 1) * limit
    jobs = db.get_recent_jobs(limit=limit, offset=offset, search=search, channel_id=channel_id)
    total = db.count_jobs(search=search, channel_id=channel_id)

    jobs_by_channel: dict[int, list[str]] = {}
    for job in jobs:
        video_id = job.get("youtube_video_id")
        job_channel_id = job.get("channel_id")
        if video_id and job_channel_id is not None:
            try:
                channel_key = int(job_channel_id)
            except Exception:
                continue
            jobs_by_channel.setdefault(channel_key, []).append(str(video_id))

    for job in jobs:
        job["youtube_view_count"] = None

    for job_channel_id, video_ids in jobs_by_channel.items():
        try:
            stats_map = youtube_manager.get_video_statistics(job_channel_id, video_ids)
        except Exception as exc:
            logger.debug(f"No se pudieron cargar estadÃ­sticas de vÃ­deos para canal {job_channel_id}: {exc}")
            continue
        for job in jobs:
            if job.get("channel_id") is None:
                continue
            try:
                if int(job["channel_id"]) != int(job_channel_id):
                    continue
            except Exception:
                continue
            video_id = job.get("youtube_video_id")
            if video_id and video_id in stats_map:
                job["youtube_view_count"] = stats_map[video_id].get("view_count")
                job["youtube_comment_count"] = stats_map[video_id].get("comment_count")

    return {
        "jobs": jobs,
        "total": total,
        "page": page,
        "limit": limit
    }

@app.post("/api/jobs/filter-new")
async def api_filter_new_posts(posts: List[PostItem], user: str = Depends(get_current_user)):
    """
    Recibe una lista de posts (con campos opcionales viral_score, etc.) y devuelve
    TODOS los que no existen aÃºn en la BD, preservando todos sus campos originales.
    Si todos son duplicados, devuelve 200 con all_duplicates=true en lugar de lanzar un error.
    """
    nuevos = [post.dict() for post in posts if not db.check_title_exists(post.title)]

    if not nuevos:
        return {
            "all_duplicates": True,
            "message": "Todas las noticias proporcionadas ya han sido procesadas anteriormente.",
            "items": []
        }

    return {
        "all_duplicates": False,
        "message": f"{len(nuevos)} noticias nuevas encontradas.",
        "items": nuevos
    }

class SaveCandidatesRequest(BaseModel):
    niche: str
    voice_id: str
    items: list

@app.post("/api/jobs/save-candidates")
async def api_save_candidates(data: SaveCandidatesRequest, user: str = Depends(get_current_user)):
    """
    Guarda lista de candidatos en el cache temporal (12h) para la selecciÃ³n manual vÃ­a Telegram.
    Devuelve un session_id corto (8 chars) que se incrusta en el callback_data de los botones.
    """
    session_id = str(uuid.uuid4())[:8]
    _candidates_cache[session_id] = {
        "expires_at": datetime.now() + timedelta(hours=12),
        "niche": data.niche,
        "voice_id": data.voice_id,
        "items": data.items
    }
    # Limpiar sesiones expiradas
    now = datetime.now()
    expired = [k for k, v in list(_candidates_cache.items()) if v["expires_at"] < now]
    for k in expired:
        del _candidates_cache[k]
    logger.info(f"Candidatos guardados: session={session_id}, niche={data.niche}, count={len(data.items)}")
    return {"session_id": session_id, "count": len(data.items), "expires_in_hours": 12}

@app.get("/api/jobs/candidate/{session_id}/{index}")
async def api_get_candidate(session_id: str, index: int, user: str = Depends(get_current_user)):
    """
    Recupera un candidato especÃ­fico por session_id e Ã­ndice (0-based).
    Usado por el workflow Telegram Handler cuando el usuario toca un botÃ³n.
    """
    session = _candidates_cache.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="SesiÃ³n expirada o no encontrada. Ejecuta el workflow de nuevo.")
    if session["expires_at"] < datetime.now():
        del _candidates_cache[session_id]
        raise HTTPException(status_code=404, detail="SesiÃ³n expirada (>12h). Ejecuta el workflow de nuevo.")
    items = session["items"]
    if index < 0 or index >= len(items):
        raise HTTPException(status_code=400, detail=f"Ãndice {index} fuera de rango (0-{len(items)-1})")
    candidate = items[index]
    return {
        "title":       candidate.get("title"),
        "content":     candidate.get("content"),
        "viral_score": candidate.get("viral_score", 0),
        "niche":       session["niche"],
        "voice_id":    session["voice_id"]
    }

@app.delete("/api/jobs/{job_id}")
async def api_delete_job(job_id: str, user: str = Depends(get_current_user)):
    if db.delete_job(job_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Trabajo no encontrado")

@app.get("/api/stats")
async def api_get_stats(channel_id: int = None, user: str = Depends(get_current_user)):
    stats = db.get_stats(channel_id=channel_id)
    return stats

# --- STUDIO PRO API ---

@app.get("/api/gallery")
async def api_get_gallery(page: int = 1, limit: int = 25, search: str = None, type: str = None, channel_id: int = None, user: str = Depends(get_current_user)):
    offset = (page - 1) * limit
    items = db.get_gallery(limit=limit, offset=offset, search=search, file_type=type, channel_id=channel_id)
    total = db.count_gallery(search=search, file_type=type, channel_id=channel_id)
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit
    }

@app.post("/api/gallery/upload")
async def api_upload_media(file: UploadFile = File(...), channel_id: int = None, user: str = Depends(get_current_user)):
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1]
    filename = f"{file_id}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())
    
    # Simple type detection
    file_type = "video" if ext.lower() in [".mp4", ".mov", ".avi"] else \
                "image" if ext.lower() in [".jpg", ".jpeg", ".png", ".webp"] else \
                "audio" if ext.lower() in [".mp3", ".wav"] else "other"
                
    db.add_media(filename, file.filename, file_type, file_path, os.path.getsize(file_path), channel_id=channel_id)
    return {"status": "success", "filename": filename}

@app.delete("/api/gallery/{media_id}")
async def api_delete_media(media_id: int, user: str = Depends(get_current_user)):
    if db.delete_media(media_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Recurso no encontrado")

@app.get("/api/settings")
async def api_get_settings(user: str = Depends(get_current_user)):
    """Devuelve todos los ajustes guardados. Los valores sensibles se enmascaran,
    pero los flags de control (2FA_ENABLED) se devuelven tal cual."""
    NON_SENSITIVE_KEYS = {
        "2FA_ENABLED",
        "DEFAULT_TTS_ENGINE",
        "DEFAULT_VOICE_ID",
        "DEFAULT_TTS_SPEED",
        "DEFAULT_MUSIC_FILENAME",
        "KIE_CURRENT_KEY_INDEX",
        "APIFY_CURRENT_KEY_INDEX",
    }
    
    with db._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT key_name, key_value FROM settings")
        rows = cursor.fetchall()
        
    settings = {}
    for row in rows:
        key = row["key_name"]
        val = row["key_value"]
        if key in NON_SENSITIVE_KEYS:
            settings[key] = val or "true"  # Default 2FA to enabled
        else:
            settings[key] = "********" if val else ""
        
    # Compat shortkeys for frontend
    for p in ["GROQ", "OPENAI", "DEEPSEEK", "OPENROUTER"]:
        if f"{p}_API_KEY" in settings:
            settings[p] = settings[f"{p}_API_KEY"]

    for p in [
        "KIE_API_KEY_1", "KIE_API_KEY_2", "KIE_API_KEY_3", "KIE_API_KEY_4", "KIE_API_KEY_5",
        "APIFY_API_KEY_1", "APIFY_API_KEY_2", "APIFY_API_KEY_3", "APIFY_API_KEY_4",
    ]:
        if p in settings:
            settings[p] = settings[p]
            
    return settings

class SaveSettingsRequest(BaseModel):
    provider: str
    api_key: str

@app.post("/api/settings")
async def api_save_settings(req: SaveSettingsRequest, user: str = Depends(get_current_user)):
    # LÃ³gica inteligente para el nombre de la llave
    key_name = req.provider.upper()
    if key_name in ["GROQ", "OPENAI", "DEEPSEEK", "OPENROUTER"]:
        key_name = f"{key_name}_API_KEY"
    
    clean_key = req.api_key.strip()
    db.set_setting(key_name, clean_key)
    return {"status": "success"}

@app.get("/api/kie/credits")
async def api_get_kie_credits(user: str = Depends(get_current_user)):
    """Returns credit balance for all configured KIE API keys."""
    results = []
    for i in range(1, 6):
        api_key = kie_manager._get_api_key_by_index(i)
        if api_key:
            credit_info = kie_manager.get_credits(api_key)
            results.append({
                "key_index": i,
                "key_name": f"KIE_API_KEY_{i}",
                "configured": True,
                "credits": credit_info.get("credits", 0),
                "status": credit_info.get("status"),
                "error": credit_info.get("msg") if credit_info.get("status") == "error" else None
            })
        else:
            results.append({
                "key_index": i,
                "key_name": f"KIE_API_KEY_{i}",
                "configured": False,
                "credits": 0,
                "status": "not_configured",
                "error": None
            })
    return {"keys": results}

@app.get("/api/templates")
async def api_get_templates(user: str = Depends(get_current_user)):
    return db.get_templates()

class OptimizeRequest(BaseModel):
    text: str
    provider: str
    template_id: int

@app.post("/api/ai/optimize")
async def api_optimize_text(req: OptimizeRequest, user: str = Depends(get_current_user)):
    templates = db.get_templates()
    template = next((t for t in templates if t["id"] == req.template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    
    try:
        optimized = ai_manager.optimize_text(req.text, req.provider, template["prompt"])
        return {"optimized_text": optimized}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- AI ASSETS & GENERATION ---

@app.post("/api/ai/generate")
async def api_generate_image(req: AiGenerateRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    """Creates an AI generation task and processes it in the background."""
    try:
        task_id, api_key = kie_manager.create_remote_task(req.prompt, req.model)
        # Register in local DB as processing
        db.add_ai_task(
            task_id,
            req.prompt,
            req.niche,
            req.model or "seedream/5-lite-text-to-image",
            channel_id=req.channel_id,
        )
        
        # Start polling in background
        background_tasks.add_task(process_ai_task_background, task_id, api_key, req)
        
        return {"status": "processing", "task_id": task_id}
    except Exception as e:
        logger.error(f"Ai Generate Start Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ai/tasks")
async def api_get_ai_tasks(page: int = 1, limit: int = 25, search: str = None, channel_id: int = None, user: str = Depends(get_current_user)):
    offset = (page - 1) * limit
    tasks = db.get_ai_tasks(limit=limit, offset=offset, search=search, channel_id=channel_id)
    total = db.count_ai_tasks(search=search, channel_id=channel_id)
    return {
        "tasks": tasks,
        "total": total,
        "page": page,
        "limit": limit
    }

@app.post("/api/ai/tasks/{task_id}/recheck")
async def api_recheck_ai_task(task_id: str, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    """Re-checks a task status without creating a new one."""
    tasks = db.get_ai_tasks(limit=1, offset=0, search=task_id)
    if not tasks or tasks[0]["task_id"] != task_id:
        raise HTTPException(status_code=404, detail="Tarea no encontrada en la BD")
        
    task_data = tasks[0]
    
    api_key = kie_manager.get_valid_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="No hay llaves de API vÃ¡lidas o con saldo.")
        
    db.update_ai_task(task_id, "processing", error_message="")
    
    req = AiGenerateRequest(
        prompt=task_data["prompt"],
        niche=task_data["niche"],
        model=task_data["model"] or "seedream/5-lite-text-to-image"
    )
    
    background_tasks.add_task(process_ai_task_background, task_id, api_key, req)
    return {"status": "processing"}

async def execute_batch_background(batch_id: str, scenes: list):
    for i, scene in enumerate(scenes):
        # 1. Gallery-First
        existing = db.find_exact_asset(scene.prompt, scene.niche)
        if existing:
            task_id = f"reuse_{str(uuid.uuid4())[:8]}"
            db.add_ai_task(task_id, scene.prompt, scene.niche, scene.model or "reused", batch_id=batch_id)
            db.update_ai_task(task_id, "completed", result_url=f"/static/uploads/{existing['filename']}", media_id=existing['id'])
            continue

        # Evitar sobrecargar Kie.ai enviando todo de golpe
        if i > 0:
            await asyncio.sleep(6)

        # 2. Remote
        try:
            task_id, api_key = kie_manager.create_remote_task(scene.prompt, scene.model)
            db.add_ai_task(task_id, scene.prompt, scene.niche, scene.model or "seedream/5-lite-text-to-image", batch_id=batch_id)
            
            gen_req = AiGenerateRequest(prompt=scene.prompt, niche=scene.niche, model=scene.model)
            asyncio.create_task(process_ai_task_background(task_id, api_key, gen_req))
        except Exception as e:
            fail_task_id = f"fail_{str(uuid.uuid4())[:8]}"
            db.add_ai_task(fail_task_id, scene.prompt, scene.niche, scene.model or "error", batch_id=batch_id)
            db.update_ai_task(fail_task_id, "failed", error_message=str(e))

@app.post("/api/ai/batch-generate")
async def api_batch_generate(req: AiBatchGenerateRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    """Processes multiple generation prompts in background stagger to rate limit, or creates drafts."""
    batch_id = f"batch_{str(uuid.uuid4())[:8]}"
    db.add_ai_batch(batch_id, len(req.scenes))
    
    if req.draft_mode:
        for scene in req.scenes:
            existing = db.find_exact_asset(scene.prompt, scene.niche)
            if existing:
                task_id = f"reuse_{str(uuid.uuid4())[:8]}"
                db.add_ai_task(task_id, scene.prompt, scene.niche, scene.model or "reused", batch_id=batch_id)
                db.update_ai_task(task_id, "completed", result_url=f"/static/uploads/{existing['filename']}", media_id=existing['id'])
            else:
                task_id = f"draft_{str(uuid.uuid4())[:8]}"
                # Usamos add_ai_task y lo forzamos a draft despuÃ©s porque add_ai_task inserta como 'processing'
                db.add_ai_task(task_id, scene.prompt, scene.niche, scene.model or "seedream/5-lite-text-to-image", batch_id=batch_id)
                db.update_ai_task(task_id, "draft")
        return {"batch_id": batch_id, "status": "draft"}
        
    background_tasks.add_task(execute_batch_background, batch_id, req.scenes)
    return {"batch_id": batch_id, "status": "processing"}

class AiTaskSubmitRequest(BaseModel):
    prompt: Optional[str] = None

@app.post("/api/ai/tasks/{task_id}/submit")
async def api_submit_ai_task(task_id: str, req: AiTaskSubmitRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    """Submits a draft AI task to Kie.ai manually."""
    with db._get_connection() as conn:
        cursor = conn.execute("SELECT * FROM ai_tasks WHERE task_id = ?", (task_id,))
        task_row = cursor.fetchone()
        
    if not task_row:
        raise HTTPException(status_code=404, detail="Task not found")
        
    task_data = dict(zip([col[0] for col in cursor.description], task_row))
    
    if task_data["status"] != "draft" and task_data["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only draft or failed tasks can be submitted")
        
    prompt_to_use = req.prompt if req.prompt and req.prompt.strip() else task_data["prompt"]
    
    # 1. Gallery-First check with the potentially new prompt
    existing = db.find_exact_asset(prompt_to_use, task_data["niche"])
    if existing:
        db.update_ai_task(task_id, "completed", result_url=f"/static/uploads/{existing['filename']}", media_id=existing['id'])
        # Actualizamos el prompt en la DB para que coincida con el final
        with db._get_connection() as conn:
            conn.execute("UPDATE ai_tasks SET prompt = ? WHERE task_id = ?", (prompt_to_use, task_id))
            conn.commit()
        return {"status": "completed"}
        
    # 2. Remote check
    with db._get_connection() as conn:
        conn.execute("UPDATE ai_tasks SET prompt = ? WHERE task_id = ?", (prompt_to_use, task_id))
        conn.commit()
        
    try:
        # PeticiÃ³n a la API
        remote_task_id, api_key = kie_manager.create_remote_task(prompt_to_use, task_data["model"])
        
        # Renombramos el ID del task local al nuevo ID remoto para que el polling funcione natural
        with db._get_connection() as conn:
            conn.execute("UPDATE ai_tasks SET task_id = ?, status = ? WHERE task_id = ?", (remote_task_id, "processing", task_id))
            conn.commit()
            
        gen_req = AiGenerateRequest(prompt=prompt_to_use, niche=task_data["niche"], model=task_data["model"])
        background_tasks.add_task(process_ai_task_background, remote_task_id, api_key, gen_req)
        
        return {"status": "processing", "task_id": remote_task_id}
    except Exception as e:
        db.update_ai_task(task_id, "failed", error_message=str(e))
        return {"status": "failed", "error": str(e)}

@app.get("/api/ai/batch-status/{batch_id}")
async def api_get_batch_status(batch_id: str, user: str = Depends(get_current_user)):
    """Returns the aggregate status of a batch and the final file list."""
    batch = db.get_ai_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
    
    tasks = db.get_tasks_by_batch(batch_id)
    
    # Construir lista de archivos final (siempre en el orden original si es posible, 
    # pero aquÃ­ confiamos en la lista de tasks de la DB)
    files = []
    for t in tasks:
        if t["status"] == "completed" and t.get("media_id"):
            # Buscar el nombre del archivo real
            with db._get_connection() as conn:
                res = conn.execute("SELECT filename FROM media WHERE id = ?", (t["media_id"],)).fetchone()
                if res:
                    files.append(res[0])

    return {
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "progress": {
            "total": batch["total_tasks"],
            "completed": batch["completed_tasks"],
            "failed": batch["failed_tasks"]
        },
        "files": files,
        "tasks": tasks
    }

@app.delete("/api/ai/tasks/{task_id}")
async def api_delete_ai_task(task_id: str, user: str = Depends(get_current_user)):
    """Deletes an AI task and associated media."""
    success = db.delete_ai_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="No se encontrÃ³ o no se pudo borrar la tarea.")
    return {"status": "success"}

async def process_ai_task_background(task_id: str, api_key: str, req: AiGenerateRequest):
    """Background loop to poll Kie.ai and download the result."""
    start_time = time.time()
    try:
        while time.time() - start_time < 300: # 5 mins max for background
            await asyncio.sleep(5)
            status, data = kie_manager.poll_task_once(task_id, api_key)
            logger.info(f"AI Task Polling: {task_id} -> Status: {status}")
            
            if status == "success":
                image_url = kie_manager._extract_image_url(data)
                if image_url:
                    try:
                        result = kie_manager._process_completed_image(image_url, req.prompt, req.niche, req.model, req.channel_id)
                        db.update_ai_task(task_id, "completed", result_url=image_url, media_id=result["media_id"])
                        logger.info(f"AI Task {task_id} completada. URL: {image_url}")
                    except Exception as download_err:
                        logger.error(f"AI Task {task_id}: Error al descargar imagen: {download_err}")
                        db.update_ai_task(task_id, "failed", error_message=f"Error descargando imagen: {download_err}")
                    return
                else:
                    logger.warning(f"AI Task {task_id}: success pero sin URL de imagen en los datos")
                    db.update_ai_task(task_id, "failed", error_message="Tarea completada pero sin URL de imagen")
                    return
            
            elif status == "fail" or status == "failed":
                error_msg = str(data.get("failMsg", "Error desconocido en Kie.ai"))
                db.update_ai_task(task_id, "failed", error_message=error_msg)
                logger.error(f"AI Task {task_id} failed: {error_msg}")
                return
            
            # If still processing, just loop
            logger.debug(f"AI Task {task_id} still {status}...")
            
        # Timeout
        db.update_ai_task(task_id, "failed", error_message="Tiempo de espera excedido (5 min)")
    except Exception as e:
        logger.error(f"Error in process_ai_task_background: {str(e)}")
        db.update_ai_task(task_id, "failed", error_message=str(e))

@app.get("/api/ai/assets/search")
async def api_search_assets(niche: str = None, prompt: str = None, limit: int = 10, user: str = Depends(get_current_user)):
    """Search for existing assets in the bank."""
    return db.find_assets(niche=niche, prompt_query=prompt, limit=limit)

@app.post("/api/ai/assets/tag")
async def api_tag_asset(req: AIAssetTagRequest, user: str = Depends(get_current_user)):
    """Tags an existing media item as an AI asset for n8n/engine searching."""
    db.tag_as_asset(
        media_id=req.media_id,
        prompt=req.prompt,
        niche=req.niche,
        asset_tag=req.asset_tag,
        is_ai=req.is_ai
    )
    return {"status": "success"}

@app.post("/api/storyboard/render")
async def api_render_storyboard(req: StoryboardRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    
    # ---- VerificaciÃ³n de tÃ­tulo Ãºnico ANTES de renderizar ----
    if req.title and db.check_title_exists(req.title, exclude_job_id=req.job_id, channel_id=req.channel_id):
        raise HTTPException(
            status_code=409,
            detail=f"TITULO_DUPLICADO: Ya existe un trabajo con el tÃ­tulo '{req.title}'. Busca otra historia viral."
        )

    # 1. Aplicar variables por defecto desde DB o guardar las nuevas aportadas por el request
    if req.tts_engine is not None:
        db.set_setting("DEFAULT_TTS_ENGINE", req.tts_engine)
    else:
        req.tts_engine = db.get_setting("DEFAULT_TTS_ENGINE") or "edge-tts"

    if req.voice_id is not None:
        db.set_setting("DEFAULT_VOICE_ID", req.voice_id)
    else:
        req.voice_id = db.get_setting("DEFAULT_VOICE_ID") or "es-ES-AlvaroNeural"

    if req.tts_speed is not None:
        db.set_setting("DEFAULT_TTS_SPEED", str(req.tts_speed))
    else:
        try:
            req.tts_speed = float(db.get_setting("DEFAULT_TTS_SPEED") or 1.0)
        except ValueError:
            req.tts_speed = 1.0

    if req.music_filename is not None:
        db.set_setting("DEFAULT_MUSIC_FILENAME", req.music_filename)
    else:
        req.music_filename = db.get_setting("DEFAULT_MUSIC_FILENAME") or ""

    if req.music_volume is not None:
        db.set_setting("DEFAULT_MUSIC_VOLUME", str(req.music_volume))
    else:
        try:
            req.music_volume = float(db.get_setting("DEFAULT_MUSIC_VOLUME") or 0.2)
        except ValueError:
            req.music_volume = 0.2

    if req.voice_volume is not None:
        db.set_setting("DEFAULT_VOICE_VOLUME", str(req.voice_volume))
    else:
        try:
            req.voice_volume = float(db.get_setting("DEFAULT_VOICE_VOLUME") or 1.0)
        except ValueError:
            req.voice_volume = 1.0

    job_to_overwrite = None
    if req.job_id:
        existing = db.get_job(req.job_id)
        if existing:
            job_to_overwrite = req.job_id

    job_id = job_to_overwrite if job_to_overwrite else f"cinema_{str(uuid.uuid4())[:8]}"
    
    import json
    scenes_json = json.dumps([s.dict() for s in req.scenes])
    
    db.save_or_update_job(
        job_id, 
        f"Storyboard: {len(req.scenes)} escenas", 
        req.niche, 
        req.voice_id, 
        status="processing",
        scenes_json=scenes_json, 
        music_filename=req.music_filename,
        music_volume=req.music_volume,
        voice_volume=req.voice_volume,
        tts_engine=req.tts_engine,
        tts_speed=req.tts_speed,
        title=req.title,
        channel_id=req.channel_id
    )
    log_job_event(
        job_id,
        "created",
        "Trabajo creado y enviado a render autom?tico.",
        status="info",
        channel_id=req.channel_id,
        details={"niche": req.niche, "scenes": len(req.scenes), "voice_id": req.voice_id},
    )
    
    background_tasks.add_task(process_storyboard_job, job_id, req)
    return {"status": "processing", "job_id": job_id}

@app.post("/api/storyboard/draft")
async def api_draft_storyboard(req: StoryboardRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    """Guarda un Short como borrador sin generar vÃ­deos automÃ¡ticamente."""

    # ---- VerificaciÃ³n de tÃ­tulo Ãºnico ANTES de crear el borrador ----
    if req.title and db.check_title_exists(req.title, exclude_job_id=req.job_id, channel_id=req.channel_id):
        raise HTTPException(
            status_code=409,
            detail=f"TITULO_DUPLICADO: Ya existe un trabajo con el tÃ­tulo '{req.title}'. Busca otra historia viral."
        )

    job_to_overwrite = None
    if req.job_id:
        existing = db.get_job(req.job_id)
        if existing:
            job_to_overwrite = req.job_id

    job_id = job_to_overwrite if job_to_overwrite else f"cinema_{str(uuid.uuid4())[:8]}"
    
    import json
    scenes_json = json.dumps([s.dict() for s in req.scenes])

    if req.music_volume is not None:
        db.set_setting("DEFAULT_MUSIC_VOLUME", str(req.music_volume))
    else:
        try:
            req.music_volume = float(db.get_setting("DEFAULT_MUSIC_VOLUME") or 0.2)
        except ValueError:
            req.music_volume = 0.2

    if req.voice_volume is not None:
        db.set_setting("DEFAULT_VOICE_VOLUME", str(req.voice_volume))
    else:
        try:
            req.voice_volume = float(db.get_setting("DEFAULT_VOICE_VOLUME") or 1.0)
        except ValueError:
            req.voice_volume = 1.0
    
    db.save_or_update_job(
        job_id, 
        f"Storyboard: {len(req.scenes)} escenas", 
        req.niche, 
        req.voice_id, 
        status="draft", # Cambio clave
        scenes_json=scenes_json, 
        music_filename=req.music_filename or db.get_setting("DEFAULT_MUSIC_FILENAME"),
        music_volume=req.music_volume if req.music_volume is not None else float(db.get_setting("DEFAULT_MUSIC_VOLUME") or 0.2),
        voice_volume=req.voice_volume if req.voice_volume is not None else float(db.get_setting("DEFAULT_VOICE_VOLUME") or 1.0),
        tts_engine=req.tts_engine or db.get_setting("DEFAULT_TTS_ENGINE") or "edge-tts",
        tts_speed=req.tts_speed or float(db.get_setting("DEFAULT_TTS_SPEED") or 1.0),
        title=req.title,
        channel_id=req.channel_id
    )
    log_job_event(
        job_id,
        "saved",
        "Trabajo guardado como borrador.",
        status="info",
        channel_id=req.channel_id,
        details={"niche": req.niche, "scenes": len(req.scenes), "title": req.title},
    )

class JobStatusRequest(BaseModel):
    status: str

@app.post("/api/jobs/{job_id}/status")
async def api_update_job_status(job_id: str, req: JobStatusRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        
    db.update_job_status(job_id, req.status)
    log_job_event(
        job_id,
        "status_changed",
        f"Estado cambiado a {req.status}",
        status="info",
        channel_id=job.get("channel_id"),
        details={"status": req.status},
    )
    return {"status": "success"}

@app.get("/api/jobs/{job_id}")
async def api_get_job_details(job_id: str, channel_id: int = None, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if channel_id is not None and job.get("channel_id") != channel_id:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")
        
    import json
    try:
        job["scenes"] = json.loads(job["scenes_json"]) if job.get("scenes_json") else []
    except Exception:
        job["scenes"] = []
        
    return job

@app.get("/api/jobs/{job_id}/logs")
async def api_get_job_logs(job_id: str, channel_id: int = None, limit: int = 100, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if channel_id is not None and job.get("channel_id") != channel_id:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")

    logs = db.get_job_logs(job_id, limit=max(1, min(limit, 200)))
    logs.reverse()
    return {
        "job": job,
        "logs": logs,
        "count": len(logs),
    }

@app.get("/api/jobs/{job_id}/statistics")
async def api_get_job_statistics(job_id: str, channel_id: int = None, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if channel_id is not None and job.get("channel_id") != channel_id:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")

    video_id = job.get("youtube_video_id")
    resolved_channel_id = channel_id or job.get("channel_id")
    stats = None
    if video_id and resolved_channel_id is not None:
        try:
            stats_map = youtube_manager.get_video_statistics(int(resolved_channel_id), [str(video_id)])
            stats = stats_map.get(str(video_id), {})
        except Exception as exc:
            logger.debug(f"No se pudieron cargar estadÃ­sticas del trabajo {job_id}: {exc}")
            stats = {}

    return {
        "job": job,
        "stats": stats or {},
        "has_video": bool(video_id),
        "channel_id": resolved_channel_id,
    }

@app.get("/api/jobs/{job_id}/publish-context")
async def api_get_job_publish_context(job_id: str, channel_id: int = None, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if channel_id is not None and job.get("channel_id") != channel_id:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")

    resolved_channel_id = channel_id or job.get("channel_id")
    channel = db.get_youtube_channel(resolved_channel_id) if resolved_channel_id else None
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    import json
    try:
        job["scenes"] = json.loads(job["scenes_json"]) if job.get("scenes_json") else []
    except Exception:
        job["scenes"] = []

    return {
        "job": job,
        "channel": serialize_youtube_channel(channel),
        "defaults": {
            "title": job.get("title") or job.get("text") or job_id,
            "description": job.get("text") or "",
            "tags": normalize_tags_input(channel.get("default_tags")),
            "privacy_status": channel.get("default_privacy_status") or "private",
            "category_id": channel.get("default_category_id") or "22",
            "license": "youtube",
            "embeddable": True,
            "public_stats_viewable": True,
            "made_for_kids": False,
            "contains_synthetic_media": False,
            "default_language": channel.get("default_language") or "es",
            "notify_subscribers": bool(channel.get("notify_subscribers")),
        },
    }

@app.get("/api/jobs/{job_id}/youtube-comments")
async def api_get_job_youtube_comments(job_id: str, channel_id: int = None, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if channel_id is not None and job.get("channel_id") != channel_id:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")

    resolved_channel_id = channel_id or job.get("channel_id")
    if not resolved_channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")
    if not job.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="El trabajo no tiene un vÃ­deo de YouTube asociado")

    try:
        comments = youtube_manager.list_video_comments(int(resolved_channel_id), str(job["youtube_video_id"]), max_results=25)
        return {
            "job": job,
            "comments": comments.get("items") or [],
            "next_page_token": comments.get("next_page_token"),
            "page_info": comments.get("page_info") or {},
        }
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/jobs/{job_id}/youtube-comments/{comment_id}/draft")
async def api_generate_comment_reply_draft(
    job_id: str,
    comment_id: str,
    req: CommentReplyDraftRequest,
    user: str = Depends(get_current_user),
):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if not job.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="El trabajo no tiene un vÃ­deo de YouTube asociado")

    resolved_channel_id = job.get("channel_id")
    if not resolved_channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")

    channel = db.get_youtube_channel(int(resolved_channel_id))
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    reply = ai_manager.generate_comment_reply(
        req.comment_text,
        provider=req.provider,
        video_title=req.video_title or job.get("title") or job.get("text") or job_id,
        channel_name=channel.get("internal_name"),
    )
    log_job_event(
        job_id,
        "comment_reply_generated",
        "Respuesta de IA preparada para un comentario.",
        status="info",
        channel_id=int(resolved_channel_id),
        details={"comment_id": comment_id, "reply_preview": reply[:500], "author_name": req.author_name},
    )
    return {"job_id": job_id, "comment_id": comment_id, "reply_text": reply}

@app.post("/api/jobs/{job_id}/youtube-comments/{comment_id}/publish")
async def api_publish_comment_reply(
    job_id: str,
    comment_id: str,
    req: CommentReplyPublishRequest,
    user: str = Depends(get_current_user),
):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if not job.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="El trabajo no tiene un vÃ­deo de YouTube asociado")

    resolved_channel_id = job.get("channel_id")
    if not resolved_channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")

    try:
        result = youtube_manager.reply_to_comment(int(resolved_channel_id), comment_id, req.reply_text)
        log_job_event(
            job_id,
            "comment_reply_published",
            "Respuesta publicada en YouTube.",
            status="success",
            channel_id=int(resolved_channel_id),
            details={"comment_id": comment_id, "reply_id": result.get("id"), "reply_text": req.reply_text},
        )
        return {"status": "success", "result": result}
    except YouTubeAuthError as exc:
        log_job_event(
            job_id,
            "comment_reply_failed",
            "No se pudo publicar la respuesta al comentario.",
            status="error",
            channel_id=int(resolved_channel_id),
            error_message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log_job_event(
            job_id,
            "comment_reply_failed",
            "No se pudo publicar la respuesta al comentario.",
            status="error",
            channel_id=int(resolved_channel_id),
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/jobs/{job_id}/publish")
async def api_publish_job_to_youtube(job_id: str, req: PublishVideoRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    resolved_channel_id = req.channel_id or job.get("channel_id")
    if not resolved_channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")
    if job.get("channel_id") is not None and int(job["channel_id"]) != int(resolved_channel_id):
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")

    channel = db.get_youtube_channel(int(resolved_channel_id))
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    video_path = resolve_job_video_path(job)
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=400, detail="No se encontrÃ³ el vÃ­deo renderizado para publicar")

    title = (req.title or job.get("title") or job.get("text") or job_id).strip()
    description = (req.description if req.description is not None else job.get("text") or "").strip()
    tags = normalize_tags_input(req.tags or channel.get("default_tags"))
    privacy_status = (req.privacy_status or channel.get("default_privacy_status") or "private").strip().lower()
    if privacy_status not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="privacy_status invÃ¡lido")
    category_id = str(req.category_id or channel.get("default_category_id") or "22")
    publish_at = parse_iso_datetime(req.publish_at)
    if publish_at:
        privacy_status = "private"

    try:
        upload_result = youtube_manager.upload_video(
            int(resolved_channel_id),
            video_path,
            {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
                "privacyStatus": privacy_status,
                "mimeType": "video/mp4",
                "publishAt": publish_at,
                "license": req.license or "youtube",
                "embeddable": True if req.embeddable is None else bool(req.embeddable),
                "publicStatsViewable": True if req.public_stats_viewable is None else bool(req.public_stats_viewable),
                "selfDeclaredMadeForKids": bool(req.made_for_kids) if req.made_for_kids is not None else False,
                "containsSyntheticMedia": bool(req.contains_synthetic_media) if req.contains_synthetic_media is not None else False,
                "defaultLanguage": req.default_language or channel.get("default_language") or "es",
                "notifySubscribers": True if req.notify_subscribers is None else bool(req.notify_subscribers),
            },
        )
        youtube_video_id = upload_result.get("id")
        youtube_video_url = f"https://www.youtube.com/watch?v={youtube_video_id}" if youtube_video_id else upload_result.get("webViewLink") or ""
        db.mark_job_published(job_id, youtube_video_id, youtube_video_url)
        log_job_event(
            job_id,
            "publish_success",
            "VÃ­deo publicado en YouTube correctamente.",
            status="success",
            channel_id=int(resolved_channel_id),
            details={"youtube_video_id": youtube_video_id, "youtube_video_url": youtube_video_url, "publish_at": publish_at},
        )
        return {
            "status": "success",
            "youtube_video_id": youtube_video_id,
            "youtube_video_url": youtube_video_url,
            "publish_at": publish_at,
        }
    except YouTubeAuthError as exc:
        log_job_event(
            job_id,
            "publish_failed",
            "La publicaci?n en YouTube ha fallado.",
            status="error",
            channel_id=int(resolved_channel_id),
            error_message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Error publicando trabajo {job_id}: {exc}")
        log_job_event(
            job_id,
            "publish_failed",
            "La publicaci?n en YouTube ha fallado.",
            status="error",
            channel_id=int(resolved_channel_id),
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/jobs/{job_id}/thumbnail")
async def api_set_job_thumbnail(job_id: str, file: UploadFile = File(...), channel_id: int = None, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if channel_id is not None and job.get("channel_id") != channel_id:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")
    if not job.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="Primero debes publicar el vÃ­deo para poder asignar miniatura")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in [".jpg", ".jpeg", ".png"]:
        raise HTTPException(status_code=400, detail="La miniatura debe ser JPG o PNG")

    tmp_path = os.path.join(BASE_DIR, "storage", "tmp", f"thumb_{uuid.uuid4().hex[:8]}{ext}")
    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
    with open(tmp_path, "wb") as buffer:
        buffer.write(await file.read())

    try:
        result = youtube_manager.set_thumbnail(int(job["channel_id"] or channel_id), job["youtube_video_id"], tmp_path)
        log_job_event(
            job_id,
            "youtube_updated",
            "Miniatura actualizada en YouTube.",
            status="success",
            channel_id=int(job["channel_id"] or channel_id),
            details={"youtube_video_id": job["youtube_video_id"]},
        )
        return {"status": "success", "thumbnail": result}
    except YouTubeAuthError as exc:
        log_job_event(
            job_id,
            "youtube_update_failed",
            "No se pudo asignar la miniatura en YouTube.",
            status="error",
            channel_id=int(job["channel_id"] or channel_id),
            error_message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

@app.post("/api/jobs/{job_id}/relink-youtube")
async def api_relink_job_youtube(job_id: str, req: RelinkYoutubeVideoRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    resolved_channel_id = req.channel_id or job.get("channel_id")
    if not resolved_channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")
    if job.get("channel_id") is not None and int(job["channel_id"]) != int(resolved_channel_id):
        raise HTTPException(status_code=404, detail="Trabajo no encontrado para este canal")

    video_id = extract_youtube_video_id(req.video_reference)
    if not video_id:
        raise HTTPException(
            status_code=400,
            detail="No se pudo extraer un video_id vÃ¡lido. Pega la URL completa de YouTube o el ID del vÃ­deo.",
        )

    youtube_video_url = f"https://www.youtube.com/watch?v={video_id}"
    db.mark_job_published(job_id, video_id, youtube_video_url)
    log_job_event(
        job_id,
        "youtube_relinked",
        "VÃ­deo de YouTube re-vinculado manualmente.",
        status="success",
        channel_id=int(resolved_channel_id),
        details={"youtube_video_id": video_id, "youtube_video_url": youtube_video_url},
    )

    updated_job = db.get_job(job_id) or {}
    return {
        "status": "success",
        "youtube_video_id": video_id,
        "youtube_video_url": youtube_video_url,
        "youtube_published_at": updated_job.get("youtube_published_at"),
    }

@app.put("/api/jobs/{job_id}/youtube")
async def api_update_job_youtube(job_id: str, req: UpdateYoutubeVideoRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    channel_id = job.get("channel_id")
    if not channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")
    if not job.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="Primero debes publicar el vÃ­deo para poder editarlo en YouTube")

    channel = db.get_youtube_channel(int(channel_id))
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    title = (req.title or job.get("title") or job.get("text") or job_id).strip()
    description = (req.description if req.description is not None else job.get("text") or "").strip()
    tags = normalize_tags_input(req.tags or channel.get("default_tags"))
    privacy_status = (req.privacy_status or channel.get("default_privacy_status") or "private").strip().lower()
    if privacy_status not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="privacy_status invÃ¡lido")
    category_id = str(req.category_id or channel.get("default_category_id") or "22")
    publish_at = parse_iso_datetime(req.publish_at)
    if publish_at:
        privacy_status = "private"

    try:
        result = youtube_manager.update_video_metadata(
            int(channel_id),
            job["youtube_video_id"],
            {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
                "privacyStatus": privacy_status,
                "publishAt": publish_at,
                "license": req.license or "youtube",
                "embeddable": True if req.embeddable is None else bool(req.embeddable),
                "publicStatsViewable": True if req.public_stats_viewable is None else bool(req.public_stats_viewable),
                "selfDeclaredMadeForKids": bool(req.made_for_kids) if req.made_for_kids is not None else False,
                "containsSyntheticMedia": bool(req.contains_synthetic_media) if req.contains_synthetic_media is not None else False,
                "defaultLanguage": req.default_language or channel.get("default_language") or "es",
            },
        )
        return {"status": "success", "youtube": result}
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Error actualizando vÃ­deo de YouTube {job_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

@app.delete("/api/jobs/{job_id}/youtube")
async def api_delete_job_youtube(job_id: str, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    channel_id = job.get("channel_id")
    if not channel_id:
        raise HTTPException(status_code=400, detail="El trabajo no tiene canal asociado")
    if not job.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="El trabajo no tiene un vÃ­deo de YouTube asociado")

    try:
        result = youtube_manager.delete_video(int(channel_id), job["youtube_video_id"])
        db.clear_job_publication(job_id)
        return {"status": "success", "youtube": result}
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Error eliminando vÃ­deo de YouTube {job_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

def resolve_background_video(niche: str, bg_name: str, custom_id: str = None, channel_id: int = None) -> str:
    """Resuelve la ruta definitiva del vÃ­deo o imagen de fondo."""
    if bg_name == "NICHE":
        bg_name = None

    def iter_galleries():
        if channel_id is not None:
            yield db.get_gallery(limit=2000, channel_id=channel_id)
        yield db.get_gallery(limit=2000)

    # 1. Por ID directo
    if custom_id:
        custom_path = os.path.join(UPLOAD_DIR, custom_id)
        if os.path.exists(custom_path):
            return custom_path
            
    # 2. Buscar en la galerÃ­a del canal y, si no aparece, en la galerÃ­a global
    if bg_name:
        for gallery in iter_galleries():
            for media in gallery:
                # Primero intentar coincidencia exacta con el nombre de archivo (mÃ¡s seguro)
                if media["filename"] == bg_name or media["original_name"] == bg_name:
                    path = os.path.join(UPLOAD_DIR, media["filename"])
                    if os.path.exists(path):
                        return path
                    
    # 3. Buscar en la carpeta backgrounds fÃ­sica por nicho
    if niche and bg_name:
        niche_path = os.path.join(BASE_DIR, "backgrounds", niche, bg_name)
        if os.path.exists(niche_path):
            return niche_path
            
    # 4. Fallback: Buscar CUALQUIER vÃ­deo en la galerÃ­a marcados con ese nicho o en general
    for gallery in iter_galleries():
        filtered = [m for m in gallery if m.get("file_type") == "video"]
        if filtered:
            import random
            chosen = random.choice(filtered)
            path = os.path.join(UPLOAD_DIR, chosen["filename"])
            if os.path.exists(path):
                return path

    # 5. Fallbacks estÃ¡ticos
    fallbacks = [
        os.path.join(BASE_DIR, "backgrounds", "default", "default.mp4"),
        os.path.join(BASE_DIR, "backgrounds", "default.mp4"),
        os.path.join(BASE_DIR, "backgrounds", "terror", "default.mp4"),
        os.path.join(BASE_DIR, "backgrounds", "curiosidades", "default.mp4")
    ]
    for fallback in fallbacks:
        if os.path.exists(fallback):
            return fallback

    # 6. Ãšltimo recurso: El primer vÃ­deo que encontremos en la galerÃ­a
    for gallery in iter_galleries():
        for media in gallery:
            if media["file_type"] == "video":
                path = os.path.join(UPLOAD_DIR, media["filename"])
                if os.path.exists(path):
                    return path
            
    return None

async def process_storyboard_job(job_id, req: StoryboardRequest):
    try:
        log_job_event(
            job_id,
            "render_started",
            "Render de storyboard iniciado.",
            status="info",
            channel_id=req.channel_id,
            details={"scenes": len(req.scenes), "music_filename": req.music_filename, "tts_engine": req.tts_engine},
        )
        scene_clips = []
        global_music_path = os.path.join(UPLOAD_DIR, req.music_filename) if req.music_filename else None
        
        for idx, scene in enumerate(req.scenes):
            scene_id = f"{job_id}_s{idx}"
            audio_path = os.path.join(BASE_DIR, "audio", f"{scene_id}.mp3")
            
            # 1. Generate Voice for this scene
            if scene.text and scene.text.strip():
                tts_manager.text_to_speech(
                    scene.text,
                    voice_id=req.voice_id,
                    output_path=audio_path,
                    engine=req.tts_engine,
                    speed=req.tts_speed
                )
            else:
                # 4 seconds of silence as requested for empty text
                tts_manager._create_silent_audio(audio_path, duration=4)

            # 2. Get Background
            bg_path = None
            import random
            
            media_name = (scene.media_filename or "").strip()
            
            # Try exact match via gallery/filesystem first (only if it looks like a real filename)
            if media_name and media_name != "NICHE" and ("." in media_name):
                bg_path = resolve_background_video(req.niche, media_name, channel_id=req.channel_id)
            
            # If not found (or NICHE or AI sent a descriptive word), pick any random niche video
            if not bg_path:
                niche_dir = os.path.join(BASE_DIR, "backgrounds", req.niche)
                if os.path.exists(niche_dir):
                    videos = [f for f in os.listdir(niche_dir) if f.lower().endswith(".mp4")]
                    if videos:
                        bg_path = os.path.join(niche_dir, random.choice(videos))
            
            # LAST RESORT: Try to find ANY video file in the entire backgrounds tree
            if not bg_path:
                for root, dirs, files in os.walk(os.path.join(BASE_DIR, "backgrounds")):
                    for file in files:
                        if file.lower().endswith(".mp4"):
                            bg_path = os.path.join(root, file)
                            break
                    if bg_path: break

            if not bg_path:
                raise ValueError(f"No se pudo encontrar ningÃºn vÃ­deo de fondo en el sistema para la escena {idx+1}. Por favor, sube al menos un vÃ­deo a la galerÃ­a.")

            scene_clips.append({
                "audio": audio_path,
                "video": bg_path,
                "text": scene.text,
                "sub_pos": scene.subtitle_pos,
                "sub_size": scene.subtitle_size,
                "show_text": scene.show_text if scene.show_text is not None else True
            })

        # 3. Final Assembly
        import time
        version = int(time.time())
        output_filename = f"{job_id}_v{version}.mp4"
        output_path = os.path.join(BASE_DIR, "shorts", output_filename)
        
        final_video = video_editor.assemble_storyboard(
            scene_clips,
            output_path,
            music_path=global_music_path,
            music_volume=req.music_volume if req.music_volume is not None else float(db.get_setting("DEFAULT_MUSIC_VOLUME") or 0.2),
            voice_volume=req.voice_volume if req.voice_volume is not None else float(db.get_setting("DEFAULT_VOICE_VOLUME") or 1.0)
        )
        db.update_job_status(job_id, "rendered", video_url=f"/static/shorts/{output_filename}")
        log_job_event(
            job_id,
            "render_finished",
            "Render de storyboard completado correctamente.",
            status="success",
            channel_id=req.channel_id,
            details={"output_filename": output_filename},
        )
        logger.info(f"Cinema Storyboard {job_id} renderizado. Esperando aprobaci?n humana.")

    except Exception as e:
        logger.error(f"Storyboard Render Failed: {str(e)}")
        db.update_job_status(job_id, "failed", error_message=str(e))
        log_job_event(
            job_id,
            "render_failed",
            "El render del storyboard ha fallado.",
            status="error",
            channel_id=req.channel_id,
            error_message=str(e),
        )
@app.get("/")
def read_root():
    return RedirectResponse(url="/dashboard")

class RenderRequest(BaseModel):
    text: str
    background_video_name: str = "default.mp4"
    niche: str = "default"
    channel_id: Optional[int] = None
    voice_id: str = "pNInz6obpgnuM07pZNoR"
    music_filename: str = None
    music_volume: float = 0.2
    voice_volume: float = 1.0
    logo_filename: str = None
    logo_position: str = "top-right"
    custom_background_filename: str = None

@app.post("/render")
async def render_short(request: RenderRequest, background_tasks: BackgroundTasks):
    """
    Triggers the generation of a short and logs it to SQLite.
    Supports Studio Pro parameters.
    """
    job_id = str(uuid.uuid4())
    audio_path = os.path.join(BASE_DIR, "audio", f"{job_id}.mp3")
    output_filename = f"{job_id}.mp4"
    output_path = os.path.join(BASE_DIR, "shorts", output_filename)

    # Log start to DB
    db.add_job(
        job_id,
        request.text,
        request.niche,
        request.voice_id,
        channel_id=request.channel_id,
        music_volume=request.music_volume,
        voice_volume=request.voice_volume,
    )
    log_job_event(
        job_id,
        "created",
        "Trabajo de render directo creado.",
        status="info",
        channel_id=request.channel_id,
        details={"niche": request.niche, "voice_id": request.voice_id},
    )

    # Background Selection Logic (Unified)
    bg_path = resolve_background_video(request.niche, request.background_video_name, request.custom_background_filename, channel_id=request.channel_id)

    music_path = os.path.join(UPLOAD_DIR, request.music_filename) if request.music_filename else None
    logo_path = os.path.join(UPLOAD_DIR, request.logo_filename) if request.logo_filename else None

    if not bg_path or not os.path.exists(bg_path):
        db.update_job_status(job_id, "failed", error_message="Fondo no encontrado")
        raise HTTPException(status_code=400, detail="Error: No hay vÃ­deos de fondo. Sube un vÃ­deo a la galerÃ­a o aÃ±ade un 'default.mp4' en storage/backgrounds/default/")

    try:
        # 1. Generate Speech
        tts_manager.text_to_speech(request.text, voice_id=request.voice_id, output_path=audio_path)
        
        # 2. Render Video (FFmpeg) with extra params
        video_editor.create_short(
            bg_path, audio_path, output_path,
            music_path=music_path, music_volume=request.music_volume, voice_volume=request.voice_volume,
            logo_path=logo_path, logo_position=request.logo_position
        )
        
        # Static URL for n8n to download
        download_url = f"/static/shorts/{output_filename}"

        # Update DB success
        db.update_job_status(job_id, "completed", video_url=download_url)
        log_job_event(
            job_id,
            "render_finished",
            "Render del v?deo directo completado correctamente.",
            status="success",
            channel_id=request.channel_id,
            details={"video_url": download_url, "output_filename": output_filename},
        )

        return {
            "status": "success",
            "job_id": job_id,
            "video_url": download_url,
            "local_path": output_path
        }

    except Exception as e:
        logger.error(f"Render failed: {e}")
        db.update_job_status(job_id, "failed", error_message=str(e))
        log_job_event(
            job_id,
            "render_failed",
            "El render del v?deo directo ha fallado.",
            status="error",
            channel_id=request.channel_id,
            error_message=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

