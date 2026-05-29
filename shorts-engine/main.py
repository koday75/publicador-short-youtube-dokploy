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
import unicodedata
from collections import Counter, defaultdict
import pyotp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response, Depends, File, UploadFile, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, List, Optional, Union
from database import JobDatabase
from ai_manager import AIManager
from leonardo_manager import LeonardoManager
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
leonardo_manager = LeonardoManager(db)
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
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    except HTTPException:
        return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    with open("static/dashboard/login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")

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

@app.get("/channels/{channel_id}/youtube-videos", response_class=HTMLResponse)
async def channel_youtube_videos_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/youtube-videos.html")

@app.get("/channels/{channel_id}/history", response_class=HTMLResponse)
async def channel_history_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/channel-history.html")

@app.get("/channels/{channel_id}/ranking", response_class=HTMLResponse)
async def channel_ranking_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/ranking.html")

@app.get("/channels/{channel_id}/insights", response_class=HTMLResponse)
async def channel_insights_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/insights.html")

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
    transition_in: Optional[str] = "fade"
    transition_in_duration: Optional[float] = 0.8
    transition_out: Optional[str] = "fade"
    transition_out_duration: Optional[float] = 0.8
    image_effect: Optional[str] = "zoom_in"
    image_zoom: Optional[float] = 1.12

class StoryboardRequest(BaseModel):
    scenes: List[StoryboardScene]
    music_filename: Optional[str] = None
    music_volume: Optional[float] = None
    voice_volume: Optional[float] = None
    intro_fade_duration: Optional[float] = 0.8
    outro_fade_duration: Optional[float] = 0.8
    music_fade_out_duration: Optional[float] = 2.0
    tail_silence_seconds: Optional[float] = 2.0
    voice_id: Optional[str] = None
    niche: str = "default"
    channel_id: Optional[int] = None
    job_id: Optional[str] = None
    tts_engine: Optional[str] = None
    tts_speed: Optional[float] = None
    video_format: Optional[str] = "vertical"
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

class MoveJobChannelRequest(BaseModel):
    target_channel_id: int
    clear_publication: bool = True

class DuplicateJobChannelRequest(BaseModel):
    target_channel_id: int
    title: Optional[str] = None

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

class LeonardoGenerateRequest(BaseModel):
    prompt: str
    channel_id: Optional[int] = None
    job_id: Optional[str] = None
    niche: str = "general"
    model_id: Optional[str] = None
    width: Optional[int] = 864
    height: Optional[int] = 1536
    num_images: Optional[int] = 1
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    public: Optional[bool] = False
    alchemy: Optional[bool] = True
    enhance_prompt: Optional[bool] = True
    prompt_magic: Optional[bool] = None
    init_generation_image_id: Optional[str] = None
    init_image_id: Optional[str] = None
    init_strength: Optional[float] = None
    transparency: Optional[str] = None
    source_media_filename: Optional[str] = None

class LeonardoVideoGenerateRequest(BaseModel):
    prompt: str
    channel_id: Optional[int] = None
    job_id: Optional[str] = None
    niche: str = "general"
    model: Optional[str] = "MOTION2"
    resolution: Optional[str] = "RESOLUTION_720"
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = 5
    frame_interpolation: Optional[bool] = True
    public: Optional[bool] = False
    seed: Optional[int] = None
    negative_prompt: Optional[str] = None
    prompt_enhance: Optional[bool] = True
    prompt_enhance_instruction: Optional[str] = None
    source_media_filename: Optional[str] = None


class LeonardoCreditUpdateRequest(BaseModel):
    balance: float

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
    visual_style_name: Optional[str] = None
    visual_style_prompt: Optional[str] = None
    visual_style_palette: Optional[str] = None
    visual_style_notes: Optional[str] = None
    leonardo_default_model_id: Optional[str] = None
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
    visual_style_name: Optional[str] = None
    visual_style_prompt: Optional[str] = None
    visual_style_palette: Optional[str] = None
    visual_style_notes: Optional[str] = None
    leonardo_default_model_id: Optional[str] = None
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
                 "LEONARDO_API_KEY",
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
    keys["LEONARDO_CREDIT_BALANCE"] = db.get_setting("LEONARDO_CREDIT_BALANCE")

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
        "visual_style_name": req.visual_style_name,
        "visual_style_prompt": req.visual_style_prompt,
        "visual_style_palette": req.visual_style_palette,
        "visual_style_notes": req.visual_style_notes,
        "leonardo_default_model_id": req.leonardo_default_model_id,
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

@app.get("/api/youtube/channels/{channel_id}/insights")
async def api_get_youtube_channel_insights(
    channel_id: int,
    refresh: bool = Query(False, description="Forzar sincronización de métricas antes de analizar"),
    user: str = Depends(get_current_user),
):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    dataset = build_channel_insights_payload(channel_id, refresh=refresh)
    if not dataset:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    analysis_input = {
        "channel": dataset.get("channel"),
        "jobs_count": dataset.get("jobs_count", 0),
        "published_jobs_count": dataset.get("published_jobs_count", 0),
        "draft_jobs_count": dataset.get("draft_jobs_count", 0),
        "best_niche": dataset.get("best_niche"),
        "best_job": dataset.get("best_job"),
        "niches": (dataset.get("niches") or [])[:12],
        "keywords": (dataset.get("keywords") or [])[:20],
        "top_jobs": (dataset.get("top_jobs") or [])[:20],
        "daily_series": (dataset.get("daily_series") or [])[-30:],
        "overview": dataset.get("overview") or {},
    }

    analysis_prompt = (
        "Eres un estratega senior de YouTube y analista de audiencias. "
        "Analiza los trabajos, los nichos, los títulos y las métricas reales del canal. "
        "Prioriza siempre vistas, likes, comentarios y engagement de los vídeos publicados sobre las hipótesis de los borradores. "
        "Detecta qué nichos, temas, ganchos y formatos funcionan mejor, qué patrones conviene repetir, "
        "qué ideas nuevas pueden crecer y qué errores conviene evitar. "
        "Si un nicho tiene pocos datos, indícalo con baja confianza. "
        "Responde solo con JSON válido usando esta estructura: "
        "{\"summary\":\"...\",\"best_niches\":[...],\"best_topics\":[...],\"audience_preferences\":[...],"
        "\"underperforming_patterns\":[...],\"content_gaps\":[...],\"recommended_formats\":[...],"
        "\"next_video_ideas\":[...],\"what_to_repeat\":[...],\"what_to_avoid\":[...],"
        "\"recommended_prompt\":\"...\",\"actions\":[...]}"
    )

    ai_result = None
    ai_error = None
    try:
        ai_result = ai_manager.analyze_channel_performance(analysis_input)
    except Exception as exc:
        ai_error = str(exc)
        logger.debug(f"Fallback de insights para canal {channel_id}: {exc}")

    if not ai_result:
        best_niche = dataset.get("best_niche") or {}
        best_job = dataset.get("best_job") or {}
        ai_result = {
            "summary": (
                f"El nicho con mejor rendimiento provisional es {best_niche.get('niche') or 'general'}. "
                f"El vídeo con más tracción hasta ahora es {best_job.get('title') or 'Sin título'}."
            ),
            "best_niches": [
                {
                    "niche": best_niche.get("niche") or "general",
                    "score": int(best_niche.get("engagement_score") or 0),
                    "why": "Mejor promedio de engagement entre los datos disponibles.",
                    "evidence": [
                        f"Vistas medias: {best_niche.get('avg_views', 0)}",
                        f"Publicaciones: {best_niche.get('jobs', 0)}",
                    ],
                }
            ] if best_niche else [],
            "best_topics": [],
            "audience_preferences": [],
            "underperforming_patterns": [],
            "content_gaps": [],
            "recommended_formats": [],
            "next_video_ideas": [],
            "what_to_repeat": [],
            "what_to_avoid": [],
            "recommended_prompt": "",
            "actions": [],
        }

    prompt_template = (
        "Actúa como estratega senior de YouTube y analista de crecimiento. "
        "Estudia los trabajos, títulos, nichos y métricas reales del canal. "
        "Prioriza vídeos publicados con vistas, likes y comentarios sobre borradores. "
        "Identifica qué nichos y temas funcionan mejor, qué patrones narrativos o de título conviene repetir, "
        "qué ideas nuevas podrían crecer y qué riesgos conviene evitar. "
        "Devuelve SOLO JSON válido con este esquema: "
        "{\"summary\":\"...\",\"best_niches\":[{\"niche\":\"...\",\"score\":0,\"why\":\"...\",\"evidence\":[\"...\"]}],"
        "\"best_topics\":[{\"topic\":\"...\",\"why\":\"...\",\"evidence\":[\"...\"]}],"
        "\"audience_preferences\":[\"...\"],"
        "\"underperforming_patterns\":[\"...\"],"
        "\"content_gaps\":[\"...\"],"
        "\"recommended_formats\":[\"...\"],"
        "\"next_video_ideas\":[{\"title\":\"...\",\"angle\":\"...\",\"why\":\"...\",\"priority\":\"alta|media|baja\"}],"
        "\"what_to_repeat\":[\"...\"],"
        "\"what_to_avoid\":[\"...\"],"
        "\"recommended_prompt\":\"...\","
        "\"actions\":[\"...\"]}"
    )

    return {
        "channel": serialize_youtube_channel(channel),
        "dataset": dataset,
        "analysis": ai_result,
        "analysis_prompt": analysis_prompt,
        "prompt_template": prompt_template,
        "ai_error": ai_error,
    }

@app.get("/api/youtube/channels/{channel_id}/ranking")
async def api_get_youtube_channel_ranking(
    channel_id: int,
    period: str = Query("month", description="day, week, month, quarter, year"),
    metric: str = Query("score", description="score, views, likes, comments, engagement"),
    refresh: bool = Query(False, description="Forzar sincronización con YouTube antes de devolver el ranking"),
    user: str = Depends(get_current_user),
):
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    return build_channel_ranking_payload(channel_id=channel_id, period=period, metric=metric, refresh=refresh)

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
        "visual_style_name": req.visual_style_name if req.visual_style_name is not None else existing.get("visual_style_name"),
        "visual_style_prompt": req.visual_style_prompt if req.visual_style_prompt is not None else existing.get("visual_style_prompt"),
        "visual_style_palette": req.visual_style_palette if req.visual_style_palette is not None else existing.get("visual_style_palette"),
        "visual_style_notes": req.visual_style_notes if req.visual_style_notes is not None else existing.get("visual_style_notes"),
        "leonardo_default_model_id": req.leonardo_default_model_id if req.leonardo_default_model_id is not None else existing.get("leonardo_default_model_id"),
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

    if source_url and not raw_text and not summary:
        logger.info(
            "Se guardará la fuente sin transcripción completa porque Apify no devolvió texto útil para %s",
            source_url,
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
            "Fuente del tema actualizada con transcripción.",
            {"source_url": source_url, "youtube_video_id": youtube_video_id, "has_text": bool(raw_text)},
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
            "Vídeo añadido al tema con su transcripción.",
            {"source_url": source_url, "youtube_video_id": youtube_video_id, "has_text": bool(raw_text)},
            source_id=source_id,
        )

    source = db.find_script_source(topic_id, source_url=source_url, youtube_video_id=youtube_video_id)
    return {"status": "success", "source": source}

@app.post("/api/scripts/topics/{topic_id}/summary")
async def api_generate_script_topic_summary(topic_id: int, user: str = Depends(get_current_user)):
    topic = db.get_script_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Tema no encontrado")

    sources = [source for source in db.list_script_sources(topic_id) if (source.get("raw_text") or "").strip()]
    if not sources:
        raise HTTPException(status_code=400, detail="Primero transcribe al menos un vídeo del tema.")

    try:
        result = ai_manager.generate_script_summary(
            sources,
            topic_title=topic.get("title"),
            topic_description=topic.get("topic"),
            max_scenes=6,
        )
    except Exception as exc:
        db.add_script_log(topic_id, "summary_failed", "No se pudo generar el resumen del tema.", {"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))

    scenes = result.get("scenes") or []
    if scenes:
        content = "\n\n".join(
            f"Escena {idx}: {str(scene.get('text') or '').strip()}"
            for idx, scene in enumerate(scenes[:6], start=1)
            if isinstance(scene, dict) and str(scene.get("text") or "").strip()
        ).strip()
    else:
        content = str(result.get("script") or result.get("summary") or "").strip()

    draft_id = db.add_script_draft(
        topic_id=topic_id,
        content=content,
        draft_type="script",
        version=len(db.list_script_drafts(topic_id)) + 1,
    )
    db.add_script_log(
        topic_id,
        "summary_created",
        "Resumen del tema generado en español.",
        {"draft_id": draft_id, "sources": len(sources), "scenes": min(len(scenes), 6) if scenes else None},
    )
    return {"status": "success", "draft_id": draft_id, "summary": result.get("summary"), "script": content, "scenes": scenes[:6]}

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


CHANNEL_INSIGHTS_STOPWORDS = {
    "a", "acerca", "al", "algo", "algunas", "algunos", "ante", "antes", "con", "contra", "cual", "cuando", "de", "del", "desde",
    "despues", "dos", "el", "ella", "ellas", "ellos", "en", "entre", "era", "eras", "eres", "es", "esa", "esas", "ese", "eso",
    "esta", "estaba", "estaban", "estamos", "estan", "estar", "este", "estos", "fue", "han", "hasta", "hay", "la", "las", "le",
    "les", "lo", "los", "mas", "mi", "mis", "muy", "ni", "no", "nos", "nosotros", "o", "para", "pero", "por", "que", "quien",
    "se", "sin", "sobre", "su", "sus", "te", "tiene", "tienen", "todo", "todos", "tu", "un", "una", "uno", "unos", "unas", "ya",
    "the", "and", "for", "with", "from", "that", "this", "those", "these", "you", "your", "our", "their", "what", "who", "when",
    "where", "why", "how", "about", "into", "over", "under", "after", "before", "then", "than", "also", "more", "most", "very",
}


def _normalize_insight_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def _extract_channel_keywords(jobs: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for job in jobs or []:
        haystack = " ".join([
            str(job.get("title") or ""),
            str(job.get("text") or ""),
            str(job.get("niche") or ""),
        ])
        tokens = re.findall(r"[a-z0-9áéíóúñü]+", haystack.lower())
        for token in tokens:
            normalized = _normalize_insight_text(token)
            if len(normalized) < 4 or normalized in CHANNEL_INSIGHTS_STOPWORDS:
                continue
            counter[normalized] += 1
    return [{"keyword": keyword, "count": count} for keyword, count in counter.most_common(limit)]


def build_channel_insights_payload(channel_id: int, refresh: bool = False) -> dict[str, Any]:
    channel = db.get_youtube_channel(channel_id)
    if not channel:
        return {}

    if refresh:
        try:
            refresh_channel_ranking_snapshots(channel_id)
        except Exception as exc:
            logger.debug(f"No se pudieron refrescar los snapshots para el analisis del canal {channel_id}: {exc}")

    overview = db.get_channel_overview(channel_id) or {}
    jobs = db.get_recent_jobs(limit=1000, channel_id=channel_id, order="DESC")
    published_jobs = [job for job in jobs if str(job.get("youtube_video_id") or "").strip()]
    snapshots = db.get_channel_ranking_snapshots(channel_id, latest_only=True)
    snapshot_map = {str(row.get("job_id") or ""): row for row in snapshots if row.get("job_id")}
    niche_map: dict[str, dict[str, Any]] = {}
    job_cards: list[dict[str, Any]] = []

    for job in jobs:
        job_id = str(job.get("job_id") or "").strip()
        niche = (job.get("niche") or "general").strip() or "general"
        snapshot = snapshot_map.get(job_id, {})
        views = int(snapshot.get("view_count") or job.get("youtube_view_count") or 0)
        likes = int(snapshot.get("like_count") or 0)
        comments = int(snapshot.get("comment_count") or job.get("youtube_comment_count") or 0)
        engagement_score = int(snapshot.get("engagement_score") or (views + likes * 5 + comments * 10))
        engagement_rate = round(((likes + comments) / views) * 100 if views else 0.0, 2)
        published = bool(str(job.get("youtube_video_id") or "").strip())
        niche_bucket = niche_map.setdefault(niche, {
            "niche": niche,
            "jobs": 0,
            "published_jobs": 0,
            "draft_jobs": 0,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "engagement_score": 0,
            "top_titles": [],
        })
        niche_bucket["jobs"] += 1
        niche_bucket["published_jobs"] += int(published)
        niche_bucket["draft_jobs"] += int(not published)
        niche_bucket["views"] += views
        niche_bucket["likes"] += likes
        niche_bucket["comments"] += comments
        niche_bucket["engagement_score"] += engagement_score
        if job.get("title"):
            niche_bucket["top_titles"].append(str(job.get("title") or "").strip())

        job_cards.append({
            "job_id": job_id,
            "title": str(job.get("title") or job.get("text") or job_id or "Sin título").strip(),
            "niche": niche,
            "status": str(job.get("status") or "unknown").strip(),
            "created_at": job.get("created_at"),
            "youtube_published_at": job.get("youtube_published_at"),
            "youtube_video_id": job.get("youtube_video_id"),
            "youtube_video_url": job.get("youtube_video_url"),
            "published": published,
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement_score": engagement_score,
            "engagement_rate": engagement_rate,
        })

    niche_items = []
    for niche_data in niche_map.values():
        jobs_count = max(1, int(niche_data["jobs"]))
        avg_views = round(niche_data["views"] / jobs_count, 2)
        avg_engagement = round(niche_data["engagement_score"] / jobs_count, 2)
        publish_rate = round((niche_data["published_jobs"] / jobs_count) * 100, 2)
        niche_items.append({
            **niche_data,
            "avg_views": avg_views,
            "avg_engagement_score": avg_engagement,
            "publish_rate": publish_rate,
            "top_titles": niche_data["top_titles"][:5],
        })

    niche_items.sort(key=lambda item: (item["avg_engagement_score"], item["avg_views"], item["published_jobs"]), reverse=True)
    keyword_items = _extract_channel_keywords(jobs, limit=12)
    top_jobs = sorted(job_cards, key=lambda item: (item["engagement_score"], item["views"], item["likes"], item["comments"]), reverse=True)[:12]
    best_niche = niche_items[0] if niche_items else None
    best_job = top_jobs[0] if top_jobs else None
    draft_jobs = [job for job in job_cards if not job["published"]]

    return {
        "channel": serialize_youtube_channel(channel),
        "overview": overview,
        "jobs_count": len(job_cards),
        "published_jobs_count": len(published_jobs),
        "draft_jobs_count": len(draft_jobs),
        "niches": niche_items,
        "keywords": keyword_items,
        "top_jobs": top_jobs,
        "best_niche": best_niche,
        "best_job": best_job,
        "jobs": job_cards,
        "daily_series": db.get_channel_ranking_daily_series(channel_id),
    }


def get_ranking_period_bounds(period: str | None) -> tuple[str, str, str]:
    normalized = (period or "month").strip().lower()
    today = datetime.now(timezone.utc).date()
    if normalized == "day":
        start_date = today
    elif normalized == "week":
        start_date = today - timedelta(days=6)
    elif normalized == "quarter":
        start_date = today - timedelta(days=89)
    elif normalized == "year":
        start_date = today - timedelta(days=364)
    else:
        normalized = "month"
        start_date = today - timedelta(days=29)
    return normalized, start_date.isoformat(), today.isoformat()


def refresh_channel_ranking_snapshots(channel_id: int) -> dict[str, Any]:
    published_jobs = [
        job for job in db.get_recent_jobs(limit=1000, channel_id=channel_id)
        if str(job.get("youtube_video_id") or "").strip()
    ]
    video_ids = [str(job.get("youtube_video_id") or "").strip() for job in published_jobs if str(job.get("youtube_video_id") or "").strip()]
    if not video_ids:
        return {"refreshed": 0, "jobs": 0}

    stats_map = youtube_manager.get_video_statistics(channel_id, video_ids)
    snapshot_date = datetime.now(timezone.utc).date().isoformat()
    refreshed = 0
    for job in published_jobs:
        video_id = str(job.get("youtube_video_id") or "").strip()
        if not video_id:
            continue
        stats = stats_map.get(video_id, {})
        db.upsert_channel_ranking_snapshot(
            channel_id=channel_id,
            job_id=str(job.get("job_id") or "").strip(),
            youtube_video_id=video_id,
            snapshot_date=snapshot_date,
            view_count=stats.get("view_count") or 0,
            like_count=stats.get("like_count") or 0,
            comment_count=stats.get("comment_count") or 0,
        )
        refreshed += 1

    return {"refreshed": refreshed, "jobs": len(published_jobs)}


def build_channel_ranking_payload(channel_id: int, period: str | None = None, metric: str | None = None, refresh: bool = False) -> dict[str, Any]:
    normalized_period, start_date, end_date = get_ranking_period_bounds(period)
    normalized_metric = (metric or "score").strip().lower()
    if normalized_metric not in {"score", "views", "likes", "comments", "engagement"}:
        normalized_metric = "score"

    refresh_info = {"refreshed": 0, "jobs": 0}
    today = datetime.now(timezone.utc).date().isoformat()
    has_today_snapshots = bool(db.get_channel_ranking_snapshots(channel_id, start_date=today, end_date=today, latest_only=False))
    if refresh or not has_today_snapshots:
        try:
            refresh_info = refresh_channel_ranking_snapshots(channel_id)
        except Exception as exc:
            logger.debug(f"No se pudieron refrescar los snapshots de ranking para el canal {channel_id}: {exc}")

    items = db.get_channel_ranking_snapshots(channel_id, start_date=start_date, end_date=end_date, latest_only=True)
    daily_series = db.get_channel_ranking_daily_series(channel_id, start_date=start_date, end_date=end_date)

    ranked_items = []
    for row in items:
        views = int(row.get("view_count") or 0)
        likes = int(row.get("like_count") or 0)
        comments = int(row.get("comment_count") or 0)
        engagement_score = int(row.get("engagement_score") or (views + likes * 5 + comments * 10))
        engagement_rate = round(((likes + comments) / views) * 100 if views else 0.0, 2)
        ranked_items.append({
            "job_id": row.get("job_id"),
            "youtube_video_id": row.get("youtube_video_id"),
            "title": row.get("job_title") or row.get("job_text") or row.get("job_id"),
            "text": row.get("job_text"),
            "niche": row.get("job_niche"),
            "job_status": row.get("job_status"),
            "video_url": row.get("youtube_video_url"),
            "thumbnail_url": row.get("thumbnail_url"),
            "youtube_published_at": row.get("youtube_published_at"),
            "job_created_at": row.get("job_created_at"),
            "snapshot_date": row.get("snapshot_date"),
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement_score": engagement_score,
            "engagement_rate": engagement_rate,
        })

    ranked_items.sort(key=lambda row: (
        -(row["engagement_score"] if normalized_metric == "score" else (
            row["views"] if normalized_metric == "views" else (
                row["likes"] if normalized_metric == "likes" else (
                    row["comments"] if normalized_metric == "comments" else row["engagement_rate"]
                )
            )
        )),
        -row["views"],
        -row["likes"],
        -row["comments"],
    ))

    summary = {
        "total_views": sum(item["views"] for item in ranked_items),
        "total_likes": sum(item["likes"] for item in ranked_items),
        "total_comments": sum(item["comments"] for item in ranked_items),
        "total_engagement_score": sum(item["engagement_score"] for item in ranked_items),
        "job_count": len(ranked_items),
        "top_job": ranked_items[0] if ranked_items else None,
        "average_views": round(sum(item["views"] for item in ranked_items) / len(ranked_items), 2) if ranked_items else 0,
        "average_engagement_rate": round(sum(item["engagement_rate"] for item in ranked_items) / len(ranked_items), 2) if ranked_items else 0,
    }

    daily_totals = []
    for row in daily_series:
        views = int(row.get("view_count") or 0)
        likes = int(row.get("like_count") or 0)
        comments = int(row.get("comment_count") or 0)
        engagement_score = int(row.get("engagement_score") or (views + likes * 5 + comments * 10))
        daily_totals.append({
            "date": row.get("snapshot_date"),
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement_score": engagement_score,
        })

    return {
        "channel": serialize_youtube_channel(db.get_youtube_channel(channel_id)),
        "period": normalized_period,
        "metric": normalized_metric,
        "start_date": start_date,
        "end_date": end_date,
        "summary": summary,
        "items": ranked_items,
        "daily_series": daily_totals,
        "refresh": refresh_info,
    }


LEONARDO_STYLE_PRESETS = {
    "3d render": "debdf72a-91a4-467b-bf61-cc02bdeb69c6",
    "bokeh": "9fdc5e8c-4d13-49b4-9ce6-5a74cbb19177",
    "cinematic": "a5632c7c-ddbb-4e2f-ba34-8456ab3ac436",
    "cinematico": "a5632c7c-ddbb-4e2f-ba34-8456ab3ac436",
    "cinematic concept": "33abbb99-03b9-4dd7-9761-ee98650b2c88",
    "creative": "6fedbf1f-4a17-45ec-84fb-92fe524a29ef",
    "dynamic": "111dc692-d470-4eec-b791-3475abac4c46",
    "fashion": "594c4a08-a522-4e0e-b7ff-e4dac4b6b622",
    "graphic design pop art": "2e74ec31-f3a4-4825-b08b-2894f6d13941",
    "graphic design vector": "1fbb6a68-9319-44d2-8d56-2957ca0ece6a",
    "hdr": "97c20e5c-1af6-4d42-b227-54d03d8f0727",
    "illustration": "645e4195-f63d-4715-a3f2-3fb1e6eb8c70",
    "ilustracion": "645e4195-f63d-4715-a3f2-3fb1e6eb8c70",
    "macro": "30c1d34f-e3a9-479a-b56f-c018bbc9c02a",
    "minimalist": "cadc8cd6-7838-4c99-b645-df76be8ba8d8",
    "minimalista": "cadc8cd6-7838-4c99-b645-df76be8ba8d8",
    "moody": "621e1c9a-6319-4bee-a12d-ae40659162fa",
    "portrait": "8e2bc543-6ee2-45f9-bcd9-594b6ce84dcd",
    "pro b&w photography": "22a9a7d2-2166-4d86-80ff-22e2643adbcf",
    "pro color photography": "7c3f932b-a572-47cb-9b9b-f20211e63b5b",
    "pro film photography": "581ba6d6-5aac-4492-bebe-54c424a0d46e",
    "portrait fashion": "0d34f8e1-46d4-428f-8ddd-4b11811fa7c9",
    "ray traced": "b504f83c-3326-4947-82e1-7fe9e839ec0f",
    "sketch (b&w)": "be8c6b58-739c-4d44-b9c1-b032ed308b61",
    "sketch (color)": "093accc3-7633-4ffd-82da-d34000dfc0d6",
    "stock photo": "5bdc3f2a-1be6-4d1c-8e77-992a30824a2c",
    "vibrant": "dee282d3-891f-4f73-ba02-7f8131e5541b",
    "vibrante": "dee282d3-891f-4f73-ba02-7f8131e5541b",
}

LEONARDO_STYLE_COMPATIBLE_MODELS = {
    "7b592283-e8a7-4c5a-9ba6-d18c31f258b9",  # Lucid Origin
    "05ce0082-2d80-4a2d-8653-4d1c85e2418e",  # Lucid Realism
    "28aeddf8-bd19-4803-80fc-79602d1a9989",  # FLUX.1 Kontext
    "de7d3faf-762f-48e0-b3b7-9d0ac3a3fcf3",  # Leonardo Phoenix 1.0
    "b2614463-296c-462a-9586-aafdb8f00e36",  # Flux Dev
    "1dd50843-d653-4516-a8e3-f0238ee453ff",  # Flux Schnell
    "6b645e3a-d64f-4341-a6d8-7a3690fbf042",  # Leonardo Phoenix 0.9
}


def normalize_visual_style_name(value: Any) -> str:
    if not value:
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_leonardo_model_id(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text):
        return text
    return None


LEONARDO_ALCHEMY_DISABLED_MODELS = {
    "aa77f04e-3eec-4034-9c07-d0f619684628",  # Leonardo Kino XL
}


def leonardo_model_supports_alchemy(model_id: str | None, model_name: str | None = None) -> bool:
    model_ref = (model_id or "").strip().lower()
    name_ref = (model_name or "").strip().lower()
    if model_ref in LEONARDO_ALCHEMY_DISABLED_MODELS:
        return False
    if "kino" in model_ref or "kino" in name_ref:
        return False
    return True


LEONARDO_VIDEO_MODELS = [
    {"id": "MOTION2", "name": "Motion 2", "kind": "video"},
    {"id": "MOTION2FAST", "name": "Motion 2 Fast", "kind": "video"},
    {"id": "VEO3", "name": "Veo 3", "kind": "video"},
    {"id": "VEO3FAST", "name": "Veo 3 Fast", "kind": "video"},
    {"id": "KLING2_1", "name": "Kling 2.1 Pro", "kind": "video"},
    {"id": "KLING2_5", "name": "Kling 2.5 Turbo", "kind": "video"},
]

LEONARDO_FALLBACK_IMAGE_MODELS = [
    {"id": "7b592283-e8a7-4c5a-9ba6-d18c31f258b9", "name": "Lucid Origin", "kind": "image"},
    {"id": "05ce0082-2d80-4a2d-8653-4d1c85e2418e", "name": "Lucid Realism", "kind": "image"},
    {"id": "de7d3faf-762f-48e0-b3b7-9d0ac3a3fcf3", "name": "Leonardo Phoenix 1.0", "kind": "image"},
    {"id": "6b645e3a-d64f-4341-a6d8-7a3690fbf042", "name": "Leonardo Phoenix 0.9", "kind": "image"},
    {"id": "b2614463-296c-462a-9586-aafdb8f00e36", "name": "Flux Dev", "kind": "image"},
    {"id": "1dd50843-d653-4516-a8e3-f0238ee453ff", "name": "Flux Schnell", "kind": "image"},
    {"id": "b24e16ff-06e3-43eb-8d33-4416c2d75876", "name": "Leonardo Lightning XL", "kind": "image"},
    {"id": "aa77f04e-3eec-4034-9c07-d0f619684628", "name": "Leonardo Kino XL", "kind": "image"},
]


def estimate_leonardo_image_cost(model_id: str | None, width: int | None, height: int | None, num_images: int | None, has_reference: bool = False, has_transparency: bool = False) -> float:
    model_ref = (model_id or "").strip().lower()
    base = 1.0
    if any(token in model_ref for token in ("flux", "lucid", "phoenix")):
        base = 0.95
    elif any(token in model_ref for token in ("gpt", "nano banana", "seedream")):
        base = 1.2
    elif any(token in model_ref for token in ("xl", "sdxl", "lightning")):
        base = 0.85
    if width and height:
        area = max(1.0, (int(width) * int(height)) / float(1024 * 1024))
        base *= max(0.85, min(1.8, area))
    if num_images:
        base *= max(1.0, min(4.0, float(num_images)))
    if has_reference:
        base += 0.25
    if has_transparency:
        base += 0.15
    return round(max(0.2, base), 2)


def normalize_leonardo_image_dimensions(width: int | None, height: int | None) -> tuple[int, int]:
    default_width = 864
    default_height = 1536
    max_dimension = 1536
    min_dimension = 16
    step = 8

    try:
        safe_width = int(width or default_width)
    except Exception:
        safe_width = default_width
    try:
        safe_height = int(height or default_height)
    except Exception:
        safe_height = default_height

    safe_width = max(min_dimension, min(max_dimension, safe_width))
    safe_height = max(min_dimension, min(max_dimension, safe_height))

    safe_width = max(min_dimension, (safe_width // step) * step)
    safe_height = max(min_dimension, (safe_height // step) * step)

    return safe_width, safe_height


def estimate_leonardo_video_cost(model: str | None, resolution: str | None, duration: int | None, frame_interpolation: bool = False) -> float:
    model_ref = (model or "").strip().upper()
    base_map = {
        "MOTION2": 3.0,
        "MOTION2FAST": 2.2,
        "VEO3": 4.8,
        "VEO3FAST": 4.2,
        "KLING2_1": 4.5,
        "KLING2_5": 5.2,
    }
    base = base_map.get(model_ref, 3.0)
    duration_value = int(duration or 5)
    if duration_value > 5:
        base += 0.8
    if duration_value > 8:
        base += 0.8
    if (resolution or "").upper() == "RESOLUTION_1080":
        base += 0.75
    elif (resolution or "").upper() == "RESOLUTION_480":
        base -= 0.35
    if frame_interpolation:
        base += 0.35
    return round(max(0.5, base), 2)


def build_channel_visual_style_context(channel: dict | None) -> tuple[str, list[str]]:
    if not channel:
        return "", []

    style_name = str(channel.get("visual_style_name") or "").strip()
    style_prompt = str(channel.get("visual_style_prompt") or "").strip()
    style_palette = str(channel.get("visual_style_palette") or "").strip()
    style_notes = str(channel.get("visual_style_notes") or "").strip()

    style_lines: list[str] = []
    if style_name:
        style_lines.append(f"Identidad visual del canal: {style_name}.")
    if style_prompt:
        style_lines.append(f"Guía de estilo: {style_prompt}.")
    if style_palette:
        style_lines.append(f"Paleta visual: {style_palette}.")
    if style_notes:
        style_lines.append(f"Notas del canal: {style_notes}.")

    preset_id = LEONARDO_STYLE_PRESETS.get(normalize_visual_style_name(style_name)) if style_name else None
    if preset_id:
        style_lines.append("Aplica un preset visual coherente con la identidad del canal.")

    if not style_lines:
        return "", []

    context = "\n".join([
        "Mantén coherencia visual con el canal.",
        *style_lines,
    ])
    return context, ([preset_id] if preset_id else [])


def leonardo_model_supports_style_ids(model_ref: str | None) -> bool:
    normalized = (model_ref or "").strip().lower()
    if not normalized:
        return False
    if normalized in LEONARDO_STYLE_COMPATIBLE_MODELS:
        return True
    return any(token in normalized for token in ("flux", "lucid", "phoenix"))


def flatten_transcript_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            cleaned = flatten_transcript_text(item)
            if cleaned:
                parts.append(cleaned)
        joined = " ".join(parts).strip()
        return joined or None
    if isinstance(value, dict):
        for key in ("text", "translation", "caption", "subtitle", "value", "transcript", "source_transcript"):
            cleaned = flatten_transcript_text(value.get(key))
            if cleaned:
                return cleaned
        for key in ("segments", "items", "captions", "subtitles"):
            cleaned = flatten_transcript_text(value.get(key))
            if cleaned:
                return cleaned
        return None
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
        or item.get("source_transcript")
        or item.get("sourceTranscript")
        or item.get("transcript_text")
        or item.get("transcriptText")
        or item.get("transcript_llm")
        or item.get("transcriptLlm")
        or item.get("translation")
        or metadata.get("transcript")
        or metadata.get("translation")
        or metadata.get("source_transcript")
        or metadata.get("sourceTranscript")
    )
    language = (
        item.get("activeLanguageCode")
        or item.get("language")
        or item.get("lang")
        or item.get("transcriptLanguage")
        or item.get("subtitlesLanguage")
        or item.get("language_code")
        or item.get("languageCode")
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
    jobs = db.get_recent_jobs(limit=limit, offset=offset, search=search, channel_id=channel_id, order="DESC")
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

@app.post("/api/jobs/{job_id}/move-channel")
async def api_move_job_channel(job_id: str, req: MoveJobChannelRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    target_channel = db.get_youtube_channel(int(req.target_channel_id))
    if not target_channel:
        raise HTTPException(status_code=404, detail="Canal destino no encontrado")

    if job.get("channel_id") is not None and int(job["channel_id"]) == int(req.target_channel_id):
        raise HTTPException(status_code=400, detail="El trabajo ya pertenece a ese canal")

    db.move_job_to_channel(job_id, int(req.target_channel_id), clear_publication=bool(req.clear_publication))
    log_job_event(
        job_id,
        "job_moved_channel",
        "Trabajo movido a otro canal.",
        status="info",
        channel_id=int(req.target_channel_id),
        details={
            "from_channel_id": job.get("channel_id"),
            "to_channel_id": int(req.target_channel_id),
            "clear_publication": bool(req.clear_publication),
        },
    )
    return {"status": "success", "job": db.get_job(job_id)}

@app.post("/api/jobs/{job_id}/duplicate-channel")
async def api_duplicate_job_channel(job_id: str, req: DuplicateJobChannelRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")

    target_channel = db.get_youtube_channel(int(req.target_channel_id))
    if not target_channel:
        raise HTTPException(status_code=404, detail="Canal destino no encontrado")

    if job.get("channel_id") is not None and int(job["channel_id"]) == int(req.target_channel_id):
        raise HTTPException(status_code=400, detail="El trabajo ya pertenece a ese canal")

    source_title = (job.get("title") or job.get("text") or job_id or "").strip()
    desired_title = (req.title or "").strip() or source_title
    new_job_id = f"{job_id}_copy_{uuid.uuid4().hex[:8]}"
    duplicated_job = db.duplicate_job_to_channel(
        source_job_id=job_id,
        target_channel_id=int(req.target_channel_id),
        new_job_id=new_job_id,
        title=desired_title,
    )
    if not duplicated_job:
        raise HTTPException(status_code=500, detail="No se pudo duplicar el trabajo")

    log_job_event(
        job_id,
        "job_duplicated_channel",
        "Trabajo duplicado en otro canal.",
        status="success",
        channel_id=int(job.get("channel_id")) if job.get("channel_id") is not None else None,
        details={
            "from_channel_id": job.get("channel_id"),
            "to_channel_id": int(req.target_channel_id),
            "new_job_id": new_job_id,
            "new_title": duplicated_job.get("title"),
        },
    )
    log_job_event(
        new_job_id,
        "job_created_from_duplicate",
        "Trabajo creado a partir de una copia.",
        status="info",
        channel_id=int(req.target_channel_id),
        details={
            "source_job_id": job_id,
            "source_channel_id": job.get("channel_id"),
        },
    )
    return {"status": "success", "job": duplicated_job}

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
        "LEONARDO_CREDIT_BALANCE",
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
        "LEONARDO_API_KEY",
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


@app.get("/api/leonardo/models")
async def api_get_leonardo_models(user: str = Depends(get_current_user)):
    return build_leonardo_model_catalog()


@app.get("/api/leonardo/credits")
async def api_get_leonardo_credits(user: str = Depends(get_current_user)):
    balance = db.get_leonardo_credit_balance(None)
    return {
        "configured": balance is not None,
        "balance": balance,
        "unit": "credits",
    }


@app.post("/api/leonardo/credits")
async def api_set_leonardo_credits(req: LeonardoCreditUpdateRequest, user: str = Depends(get_current_user)):
    db.set_leonardo_credit_balance(req.balance)
    return {
        "status": "success",
        "balance": db.get_leonardo_credit_balance(None),
        "unit": "credits",
    }

@app.get("/api/templates")
async def api_get_templates(user: str = Depends(get_current_user)):
    return db.get_templates()

class OptimizeRequest(BaseModel):
    text: str
    provider: str
    template_id: int

class SceneTranslateRequest(BaseModel):
    text: str
    target_language: str

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

@app.post("/api/ai/translate-scene")
async def api_translate_scene(req: SceneTranslateRequest, user: str = Depends(get_current_user)):
    try:
        translated = ai_manager.translate_scene_text(req.text, req.target_language)
        return {"translated_text": translated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def build_leonardo_model_catalog(channel_id: int | None = None) -> dict[str, list[dict]]:
    try:
        platform_models = leonardo_manager.list_platform_models()
    except Exception as exc:
        logger.warning("No se pudieron cargar los modelos públicos de Leonardo: %s", exc)
        platform_models = []

    image_models = []
    seen_ids: set[str] = set()
    for model in platform_models:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("id") or "").strip()
        if not model_id or model_id in seen_ids:
            continue
        if str(model.get("kind") or "").lower() != "image":
            continue
        seen_ids.add(model_id)
        image_models.append({
            "id": model_id,
            "name": model.get("name") or model_id,
            "description": model.get("description") or "",
            "kind": "image",
            "supports_alchemy": leonardo_model_supports_alchemy(model_id, model.get("name")),
            "estimated_cost": estimate_leonardo_image_cost(model_id, 864, 1536, 1),
        })

    if not image_models:
        image_models = [
            {
                "id": item["id"],
                "name": item["name"],
                "description": "",
                "kind": "image",
                "supports_alchemy": leonardo_model_supports_alchemy(item["id"], item["name"]),
                "estimated_cost": estimate_leonardo_image_cost(item["id"], 864, 1536, 1),
            }
            for item in LEONARDO_FALLBACK_IMAGE_MODELS
        ]

    video_models = [
        {
            "id": item["id"],
            "name": item["name"],
            "description": "Modelo oficial de vídeo de Leonardo.",
            "kind": "video",
            "supports_alchemy": False,
            "estimated_cost": estimate_leonardo_video_cost(item["id"], "RESOLUTION_720", 5, True),
        }
        for item in LEONARDO_VIDEO_MODELS
    ]

    balance = db.get_leonardo_credit_balance(None)
    return {
        "image_models": image_models,
        "video_models": video_models,
        "credit_balance": balance,
        "credit_unit": "credits",
    }


def extract_leonardo_generation_cost(data: dict) -> tuple[float | None, str | None]:
    try:
        return leonardo_manager.extract_generation_cost(data)
    except Exception:
        return None, None


def reconcile_leonardo_credit_balance(task_id: str, data: dict, fallback_cost: float | None = None):
    cost_amount, cost_unit = extract_leonardo_generation_cost(data)
    if cost_amount is None and fallback_cost is not None:
        cost_amount = fallback_cost
        cost_unit = "credits"

    if cost_amount is None:
        return None, None

    db.update_ai_task_cost(task_id, cost_amount, cost_unit or "credits")
    current_balance = db.get_leonardo_credit_balance(None)
    if current_balance is not None:
        updated_balance = max(0.0, float(current_balance) - float(cost_amount))
        db.set_leonardo_credit_balance(updated_balance)
        return cost_amount, updated_balance
    return cost_amount, None

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

@app.post("/api/leonardo/generate")
async def api_generate_leonardo_image(req: LeonardoGenerateRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    if not leonardo_manager.is_configured():
        raise HTTPException(status_code=400, detail="LEONARDO_API_KEY no está configurada.")

    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacío.")

    task_id = f"leo_{uuid.uuid4().hex[:12]}"
    channel = db.get_youtube_channel(int(req.channel_id)) if req.channel_id else None
    channel_style_context, channel_style_ids = build_channel_visual_style_context(channel)
    requested_model = normalize_leonardo_model_id(req.model_id)
    channel_model = normalize_leonardo_model_id(channel.get("leonardo_default_model_id") if channel else None)
    global_model = normalize_leonardo_model_id(db.get_setting("LEONARDO_DEFAULT_MODEL_ID"))
    model_ref = (
        requested_model
        or channel_model
        or global_model
        or ""
    ).strip() or "leonardo"
    applied_style_ids = channel_style_ids if leonardo_model_supports_style_ids(model_ref) else []
    supports_alchemy = leonardo_model_supports_alchemy(model_ref)
    effective_prompt = prompt
    if channel_style_context:
        effective_prompt = f"{prompt}\n\n{channel_style_context}"
    init_image_id = req.init_image_id

    db.add_ai_task(
        task_id,
        effective_prompt,
        req.niche,
        model_ref,
        channel_id=req.channel_id,
        provider="leonardo",
    )

    try:
        if not init_image_id and req.source_media_filename:
            media = db.get_media_by_filename(req.source_media_filename, channel_id=req.channel_id)
            if media and media.get("file_path") and os.path.exists(str(media["file_path"])):
                media_type = str(media.get("file_type") or "").lower()
                if media_type.startswith("image"):
                    uploaded = leonardo_manager.upload_init_image(str(media["file_path"]))
                    init_image_id = uploaded.get("id")

        safe_width, safe_height = normalize_leonardo_image_dimensions(req.width, req.height)

        generation_id, raw_data = leonardo_manager.create_image_generation(
            effective_prompt,
            model_id=requested_model or channel_model or global_model,
            style_ids=applied_style_ids,
            width=safe_width,
            height=safe_height,
            num_images=req.num_images or 1,
            negative_prompt=req.negative_prompt,
            seed=req.seed,
            public=bool(req.public),
            alchemy=bool(req.alchemy) and supports_alchemy,
            enhance_prompt=bool(req.enhance_prompt),
            prompt_magic=req.prompt_magic,
            init_generation_image_id=req.init_generation_image_id,
            init_image_id=init_image_id,
            init_strength=req.init_strength,
            transparency=req.transparency,
            channel_id=req.channel_id,
            job_id=req.job_id,
        )
    except Exception as exc:
        db.update_ai_task(task_id, "failed", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    with db._get_connection() as conn:
        conn.execute(
            "UPDATE ai_tasks SET provider = ?, external_id = ?, model = ? WHERE task_id = ?",
            ("leonardo", generation_id, model_ref, task_id),
        )
        conn.commit()

    if req.job_id:
        log_job_event(
            req.job_id,
            "leonardo_generation_started",
            "Generación de Leonardo iniciada.",
            status="info",
            channel_id=req.channel_id,
            details={
                "task_id": task_id,
                "generation_id": generation_id,
                "model": model_ref,
                "style_name": channel.get("visual_style_name") if channel else None,
                "style_ids_applied": bool(applied_style_ids),
            },
        )

    background_tasks.add_task(process_leonardo_task_background, task_id, generation_id, req)
    return {"status": "processing", "task_id": task_id, "generation_id": generation_id, "provider": "leonardo"}

@app.get("/api/leonardo/generations/{generation_id}")
async def api_get_leonardo_generation(generation_id: str, user: str = Depends(get_current_user)):
    try:
        status, data = leonardo_manager.poll_generation_once(generation_id)
        generation = data.get("generations_by_pk") or data.get("generation") or {}
        return {
            "generation_id": generation_id,
            "status": status,
            "generated_images": generation.get("generated_images") or [],
            "raw": data,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/leonardo/generate-video")
async def api_generate_leonardo_video(req: LeonardoVideoGenerateRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    if not leonardo_manager.is_configured():
        raise HTTPException(status_code=400, detail="LEONARDO_API_KEY no está configurada.")

    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacío.")

    channel = db.get_youtube_channel(int(req.channel_id)) if req.channel_id else None
    channel_style_context, channel_style_ids = build_channel_visual_style_context(channel)
    model_ref = (req.model or "MOTION2").strip() or "MOTION2"
    effective_prompt = prompt
    if channel_style_context:
        effective_prompt = f"{prompt}\n\n{channel_style_context}"

    task_id = f"lev_{uuid.uuid4().hex[:12]}"
    init_image_id = None
    init_image_type = "UPLOADED"

    db.add_ai_task(
        task_id,
        effective_prompt,
        req.niche,
        model_ref,
        channel_id=req.channel_id,
        provider="leonardo",
    )

    try:
        if req.source_media_filename:
            media = db.get_media_by_filename(req.source_media_filename, channel_id=req.channel_id)
            if media and media.get("file_path") and os.path.exists(str(media["file_path"])):
                media_type = str(media.get("file_type") or "").lower()
                if media_type.startswith("image"):
                    uploaded = leonardo_manager.upload_init_image(str(media["file_path"]))
                    init_image_id = uploaded.get("id")
                elif media_type.startswith("video"):
                    raise HTTPException(status_code=400, detail="El generador de vídeo necesita una imagen como referencia, no un vídeo.")
        if not init_image_id:
            raise HTTPException(status_code=400, detail="Selecciona una imagen de la escena o de la galería para poder animarla con Leonardo.")

        generation_id, _raw_data = leonardo_manager.create_video_generation(
            effective_prompt,
            image_id=init_image_id,
            image_type=init_image_type,
            model=model_ref,
            resolution=req.resolution or "RESOLUTION_720",
            width=req.width,
            height=req.height,
            duration=req.duration,
            frame_interpolation=req.frame_interpolation,
            public=bool(req.public),
            seed=req.seed,
            negative_prompt=req.negative_prompt,
            prompt_enhance=req.prompt_enhance,
            prompt_enhance_instruction=req.prompt_enhance_instruction,
            style_ids=channel_style_ids,
        )
    except HTTPException:
        db.update_ai_task(task_id, "failed", error_message="No se pudo iniciar la generación de vídeo.")
        raise
    except Exception as exc:
        db.update_ai_task(task_id, "failed", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    with db._get_connection() as conn:
        conn.execute(
            "UPDATE ai_tasks SET provider = ?, external_id = ?, model = ? WHERE task_id = ?",
            ("leonardo", generation_id, model_ref, task_id),
        )
        conn.commit()

    if req.job_id:
        log_job_event(
            req.job_id,
            "leonardo_video_generation_started",
            "Generación de vídeo con Leonardo iniciada.",
            status="info",
            channel_id=req.channel_id,
            details={
                "task_id": task_id,
                "generation_id": generation_id,
                "model": model_ref,
                "style_name": channel.get("visual_style_name") if channel else None,
                "style_ids_applied": bool(channel_style_ids),
            },
        )

    background_tasks.add_task(process_leonardo_video_task_background, task_id, generation_id, req)
    return {"status": "processing", "task_id": task_id, "generation_id": generation_id, "provider": "leonardo", "media_type": "video"}

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

async def process_leonardo_task_background(task_id: str, generation_id: str, req: LeonardoGenerateRequest):
    """Background loop to poll Leonardo and download the generated image."""
    start_time = time.time()
    channel = db.get_youtube_channel(int(req.channel_id)) if req.channel_id else None
    model_ref = (
        normalize_leonardo_model_id(req.model_id)
        or normalize_leonardo_model_id(channel.get("leonardo_default_model_id") if channel else None)
        or normalize_leonardo_model_id(db.get_setting("LEONARDO_DEFAULT_MODEL_ID"))
        or "leonardo"
    )
    safe_width, safe_height = normalize_leonardo_image_dimensions(req.width, req.height)
    cost_recorded = False
    try:
        while time.time() - start_time < 600:  # 10 min max
            await asyncio.sleep(6)
            status, data = leonardo_manager.poll_generation_once(generation_id)
            generation = data.get("generations_by_pk") or data.get("generation") or {}
            image_url = leonardo_manager.extract_generated_image_url(data)
            logger.info("Leonardo task polling: %s -> status=%s", task_id, status)

            if status in {"FAILED", "FAIL", "ERROR"}:
                error_msg = (
                    generation.get("failureReason")
                    or generation.get("failure_reason")
                    or data.get("error")
                    or data.get("message")
                    or "Error desconocido en Leonardo"
                )
                db.update_ai_task(task_id, "failed", error_message=str(error_msg))
                if req.job_id:
                    log_job_event(
                        req.job_id,
                        "leonardo_generation_failed",
                        "La generación de Leonardo falló.",
                        status="error",
                        channel_id=req.channel_id,
                        error_message=str(error_msg),
                        details={"task_id": task_id, "generation_id": generation_id},
                )
                return

            if status in {"COMPLETE", "COMPLETED", "SUCCESS"} and not cost_recorded:
                cost_amount, cost_unit = reconcile_leonardo_credit_balance(task_id, data, fallback_cost=estimate_leonardo_image_cost(
                    model_ref,
                    safe_width,
                    safe_height,
                    req.num_images,
                    bool(req.init_image_id or req.init_generation_image_id or req.source_media_filename),
                    bool(req.transparency),
                ))
                cost_recorded = True
                logger.info(
                    "Leonardo image task %s cost recorded: amount=%s unit=%s",
                    task_id,
                    cost_amount,
                    cost_unit,
                )
                if req.job_id and cost_amount is not None:
                    log_job_event(
                        req.job_id,
                        "leonardo_generation_cost_recorded",
                        "Coste de Leonardo registrado para la imagen.",
                        status="info",
                        channel_id=req.channel_id,
                        details={
                            "task_id": task_id,
                            "generation_id": generation_id,
                            "cost_amount": cost_amount,
                            "cost_unit": cost_unit or "credits",
                        },
                    )

            if status in {"COMPLETE", "COMPLETED", "SUCCESS"} and image_url:
                try:
                    result = leonardo_manager.download_generated_image(
                        image_url,
                        req.prompt,
                        req.niche,
                        model_ref,
                        req.channel_id,
                    )
                    db.update_ai_task(task_id, "completed", result_url=image_url, media_id=result["media_id"])
                    if req.job_id:
                        log_job_event(
                            req.job_id,
                            "leonardo_generation_completed",
                            "Imagen de Leonardo descargada y guardada en la galería.",
                            status="success",
                            channel_id=req.channel_id,
                            details={
                                "task_id": task_id,
                                "generation_id": generation_id,
                                "media_id": result["media_id"],
                                "filename": result["filename"],
                            },
                        )
                    return
                except Exception as download_err:
                    logger.error("Leonardo task %s: error descargando imagen: %s", task_id, download_err)
                    db.update_ai_task(task_id, "failed", error_message=f"Error descargando imagen: {download_err}")
                    return

            if status in {"COMPLETE", "COMPLETED", "SUCCESS"} and not image_url:
                logger.info("Leonardo task %s completada sin URL todavía; continuamos esperando.", task_id)

        db.update_ai_task(task_id, "failed", error_message="Tiempo de espera excedido (10 min)")
        if req.job_id:
            log_job_event(
                req.job_id,
                "leonardo_generation_timeout",
                "Tiempo de espera excedido en Leonardo.",
                status="error",
                channel_id=req.channel_id,
                error_message="Tiempo de espera excedido (10 min)",
                details={"task_id": task_id, "generation_id": generation_id},
            )
    except Exception as e:
        logger.error("Error in process_leonardo_task_background: %s", str(e))
        db.update_ai_task(task_id, "failed", error_message=str(e))

async def process_leonardo_video_task_background(task_id: str, generation_id: str, req: LeonardoVideoGenerateRequest):
    """Background loop to poll Leonardo video generations and download the rendered clip."""
    start_time = time.time()
    channel = db.get_youtube_channel(int(req.channel_id)) if req.channel_id else None
    model_ref = (req.model or "MOTION2").strip() or "MOTION2"
    cost_recorded = False
    try:
        while time.time() - start_time < 900:  # 15 min max
            await asyncio.sleep(8)
            status, data = leonardo_manager.poll_generation_once(generation_id)
            generation = data.get("generations_by_pk") or data.get("generation") or {}
            video_url = leonardo_manager.extract_generated_video_url(data)
            logger.info("Leonardo video task polling: %s -> status=%s", task_id, status)

            if status in {"FAILED", "FAIL", "ERROR"}:
                error_msg = (
                    generation.get("failureReason")
                    or generation.get("failure_reason")
                    or data.get("error")
                    or data.get("message")
                    or "Error desconocido en Leonardo"
                )
                db.update_ai_task(task_id, "failed", error_message=str(error_msg))
                if req.job_id:
                    log_job_event(
                        req.job_id,
                        "leonardo_video_generation_failed",
                        "La generación de vídeo con Leonardo falló.",
                        status="error",
                        channel_id=req.channel_id,
                        error_message=str(error_msg),
                        details={"task_id": task_id, "generation_id": generation_id},
                )
                return

            if status in {"COMPLETE", "COMPLETED", "SUCCESS"} and not cost_recorded:
                cost_amount, cost_unit = reconcile_leonardo_credit_balance(task_id, data, fallback_cost=estimate_leonardo_video_cost(
                    model_ref,
                    req.resolution,
                    req.duration,
                    bool(req.frame_interpolation),
                ))
                cost_recorded = True
                logger.info(
                    "Leonardo video task %s cost recorded: amount=%s unit=%s",
                    task_id,
                    cost_amount,
                    cost_unit,
                )
                if req.job_id and cost_amount is not None:
                    log_job_event(
                        req.job_id,
                        "leonardo_video_generation_cost_recorded",
                        "Coste de Leonardo registrado para el vídeo.",
                        status="info",
                        channel_id=req.channel_id,
                        details={
                            "task_id": task_id,
                            "generation_id": generation_id,
                            "cost_amount": cost_amount,
                            "cost_unit": cost_unit or "credits",
                        },
                    )

            if status in {"COMPLETE", "COMPLETED", "SUCCESS"} and video_url:
                try:
                    result = leonardo_manager.download_generated_video(
                        video_url,
                        req.prompt,
                        req.niche,
                        model_ref,
                        req.channel_id,
                    )
                    db.update_ai_task(task_id, "completed", result_url=video_url, media_id=result["media_id"])
                    if req.job_id:
                        log_job_event(
                            req.job_id,
                            "leonardo_video_generation_completed",
                            "Vídeo de Leonardo descargado y guardado en la galería.",
                            status="success",
                            channel_id=req.channel_id,
                            details={
                                "task_id": task_id,
                                "generation_id": generation_id,
                                "media_id": result["media_id"],
                                "filename": result["filename"],
                            },
                        )
                    return
                except Exception as download_err:
                    logger.error("Leonardo video task %s: error descargando vídeo: %s", task_id, download_err)
                    db.update_ai_task(task_id, "failed", error_message=f"Error descargando vídeo: {download_err}")
                    if req.job_id:
                        log_job_event(
                            req.job_id,
                            "leonardo_video_generation_failed",
                            "No se pudo descargar el vídeo generado por Leonardo.",
                            status="error",
                            channel_id=req.channel_id,
                            error_message=str(download_err),
                            details={"task_id": task_id, "generation_id": generation_id},
                        )
                    return

            if status in {"COMPLETE", "COMPLETED", "SUCCESS"} and not video_url:
                logger.info("Leonardo video task %s completada sin URL todavía; continuamos esperando.", task_id)

        db.update_ai_task(task_id, "failed", error_message="Tiempo de espera excedido (15 min)")
        if req.job_id:
            log_job_event(
                req.job_id,
                "leonardo_video_generation_timeout",
                "Tiempo de espera excedido en Leonardo.",
                status="error",
                channel_id=req.channel_id,
                error_message="Tiempo de espera excedido (15 min)",
                details={"task_id": task_id, "generation_id": generation_id},
            )
    except Exception as e:
        logger.error("Error in process_leonardo_video_task_background: %s", str(e))
        db.update_ai_task(task_id, "failed", error_message=str(e))
        if req.job_id:
            log_job_event(
                req.job_id,
                "leonardo_video_generation_failed",
                "La generación de vídeo con Leonardo falló por un error interno.",
                status="error",
                channel_id=req.channel_id,
                error_message=str(e),
                details={"task_id": task_id, "generation_id": generation_id},
            )

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
        channel_id=req.channel_id,
        intro_fade_duration=req.intro_fade_duration,
        outro_fade_duration=req.outro_fade_duration,
        music_fade_out_duration=req.music_fade_out_duration,
        tail_silence_seconds=req.tail_silence_seconds,
        video_format=normalize_storyboard_video_format(req.video_format)[0]
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
        channel_id=req.channel_id,
        intro_fade_duration=req.intro_fade_duration,
        outro_fade_duration=req.outro_fade_duration,
        music_fade_out_duration=req.music_fade_out_duration,
        tail_silence_seconds=req.tail_silence_seconds,
        video_format=normalize_storyboard_video_format(req.video_format)[0]
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
        storyboard_format, canvas_width, canvas_height, aspect_label = normalize_storyboard_video_format(req.video_format)
        log_job_event(
            job_id,
            "render_started",
            "Render de storyboard iniciado.",
            status="info",
            channel_id=req.channel_id,
            details={
                "scenes": len(req.scenes),
                "music_filename": req.music_filename,
                "tts_engine": req.tts_engine,
                "video_format": storyboard_format,
                "canvas_width": canvas_width,
                "canvas_height": canvas_height,
            },
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
                "show_text": scene.show_text if scene.show_text is not None else True,
                "transition_in": scene.transition_in or "fade",
                "transition_in_duration": scene.transition_in_duration if scene.transition_in_duration is not None else 0.8,
                "transition_out": scene.transition_out or "fade",
                "transition_out_duration": scene.transition_out_duration if scene.transition_out_duration is not None else 0.8,
                "image_effect": scene.image_effect or "zoom_in",
                "image_zoom": scene.image_zoom if scene.image_zoom is not None else 1.12,
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
            voice_volume=req.voice_volume if req.voice_volume is not None else float(db.get_setting("DEFAULT_VOICE_VOLUME") or 1.0),
            intro_fade_duration=req.intro_fade_duration if req.intro_fade_duration is not None else 0.8,
            outro_fade_duration=req.outro_fade_duration if req.outro_fade_duration is not None else 0.8,
            music_fade_out_duration=req.music_fade_out_duration if req.music_fade_out_duration is not None else 2.0,
            tail_silence_seconds=req.tail_silence_seconds if req.tail_silence_seconds is not None else 2.0,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        db.update_job_status(job_id, "rendered", video_url=f"/static/shorts/{output_filename}")
        log_job_event(
            job_id,
            "render_finished",
            "Render de storyboard completado correctamente.",
            status="success",
            channel_id=req.channel_id,
            details={"output_filename": output_filename, "video_format": storyboard_format, "aspect_ratio": aspect_label},
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

def normalize_storyboard_video_format(value: Optional[str]) -> tuple[str, int, int, str]:
    normalized = (value or "vertical").strip().lower()
    if normalized in {"16:9", "landscape", "wide", "youtube", "youtube-wide", "youtube_landscape"}:
        return "landscape", 1920, 1080, "16 / 9"
    return "vertical", 1080, 1920, "9 / 16"

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
        intro_fade_duration=0.8,
        outro_fade_duration=0.8,
        music_fade_out_duration=2.0,
        tail_silence_seconds=2.0,
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

