from __future__ import annotations

import base64
import hashlib
import sqlite3
import os
import uuid
import logging
import time
import asyncio
import json
import pyotp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response, Depends, File, UploadFile, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, List, Optional, Union
from database import JobDatabase
from ai_manager import AIManager
from elevenlabs_manager import ElevenLabsManager
from video_editor import VideoEditor
from kie_manager import KieAiManager
from youtube_service import YouTubeChannelService, YouTubeAuthError

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from datetime import datetime, timedelta

app = FastAPI(title="Shorts Generation Engine")

# ── Cache temporal de candidatos para selección Telegram ─────────────────────
# Clave: session_id (8 chars), Valor: { expires_at, niche, voice_id, items[] }
_candidates_cache: dict = {}

# Security Configuration
DASHBOARD_PASSWORD = (os.getenv("DASHBOARD_PASSWORD") or "admin123").strip().strip('"').strip("'") or "admin123"
logger.info(f"Cargando configuración: DASHBOARD_PASSWORD detectada con longitud {len(DASHBOARD_PASSWORD)}")
TOTP_SECRET = (os.getenv("DASHBOARD_TOTP_SECRET") or "").strip()

if not TOTP_SECRET:
    # Stable fallback so 2FA survives restarts when the secret is not provided.
    digest = hashlib.sha256(DASHBOARD_PASSWORD.encode("utf-8")).digest()
    TOTP_SECRET = base64.b32encode(digest).decode("utf-8").rstrip("=")
    logger.warning("="*50)
    logger.warning("CONFIGURACIÓN DE SEGURIDAD 2FA (TOTP)")
    logger.warning("DASHBOARD_TOTP_SECRET no estaba configurado; se usará una clave derivada estable.")
    logger.warning("Escanea este código o introdúcelo en Authenticator:")
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
    return await render_dashboard_file(request, "static/dashboard/index.html")

@app.get("/channels/{channel_id}", response_class=HTMLResponse)
async def channel_workspace_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/channel-workspace.html")

@app.get("/channels/{channel_id}/history", response_class=HTMLResponse)
async def channel_history_page(channel_id: int, request: Request):
    return await render_dashboard_file(request, "static/dashboard/channel-history.html")

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/jobs.html")

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

@app.get("/youtube-channels", response_class=HTMLResponse)
async def youtube_channels_page(request: Request):
    return await render_dashboard_file(request, "static/dashboard/youtube-channels.html")

# --- STORYBOARD MODELS ---
class StoryboardScene(BaseModel):
    text: Optional[str] = ""
    media_filename: Optional[str] = ""
    subtitle_pos: Optional[Union[int, str]] = 5
    subtitle_size: Optional[Union[int, str]] = 48

class StoryboardRequest(BaseModel):
    scenes: List[StoryboardScene]
    music_filename: Optional[str] = None
    voice_id: Optional[str] = None
    niche: str = "default"
    channel_id: Optional[int] = None
    job_id: Optional[str] = None
    tts_engine: Optional[str] = None
    tts_speed: Optional[float] = None
    title: Optional[str] = None  # Título único del Short (para deduplicación)

class LoginRequest(BaseModel):
    password: str

class Verify2FARequest(BaseModel):
    temp_token: str
    code: str

class AiGenerateRequest(BaseModel):
    prompt: str
    niche: str = "general"
    model: Optional[str] = None

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
                 "KIE_API_KEY_1", "KIE_API_KEY_2", "KIE_API_KEY_3", "KIE_API_KEY_4", "KIE_API_KEY_5"]:
        val = db.get_setting(prov)
        keys[prov] = "********" if val else None

    # Ajustes varios
    keys["2FA_ENABLED"] = db.get_setting("2FA_ENABLED", "true")
    keys["KIE_CURRENT_KEY_INDEX"] = db.get_setting("KIE_CURRENT_KEY_INDEX", "1")

    return keys

@app.get("/api/youtube/channels")
async def api_list_youtube_channels(user: str = Depends(get_current_user)):
    channels = [serialize_youtube_channel(ch) for ch in db.list_youtube_channels()]
    return {"items": channels}

@app.post("/api/youtube/channels")
async def api_create_youtube_channel(req: YouTubeChannelCreateRequest, user: str = Depends(get_current_user)):
    if not req.internal_name or not req.internal_name.strip():
        raise HTTPException(status_code=400, detail="El nombre interno es obligatorio.")

    privacy = req.default_privacy_status.strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="default_privacy_status inválido.")

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

@app.put("/api/youtube/channels/{channel_id}")
async def api_update_youtube_channel(channel_id: int, req: YouTubeChannelUpdateRequest, user: str = Depends(get_current_user)):
    existing = db.get_youtube_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    privacy = req.default_privacy_status.strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="default_privacy_status inválido.")

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
        raise HTTPException(status_code=400, detail="Faltan parámetros OAuth.")

    try:
        channel = youtube_manager.handle_oauth_callback(code, state)
        if channel:
            return RedirectResponse(url=f"/youtube-channels?oauth=success&id={channel['id']}")
        return RedirectResponse(url="/youtube-channels?oauth=success")
    except YouTubeAuthError as exc:
        logger.error(f"Callback OAuth falló: {exc}")
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

@app.post("/api/auth/login")
async def api_login(req: LoginRequest, response: Response):
    attempt = req.password.strip().strip('"').strip("'")
    if attempt == DASHBOARD_PASSWORD:
        # Verificar si 2FA está activo
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
            
    raise HTTPException(status_code=401, detail="Contraseña incorrecta")

@app.post("/api/auth/verify-2fa")
async def api_verify_2fa(req: Verify2FARequest, response: Response):
    if totp.verify(req.code):
        session_id = str(uuid.uuid4())
        active_sessions.add(session_id)
        response.set_cookie(key="session_id", value=session_id, httponly=True)
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Código 2FA inválido")

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Error de validación 422: {exc.errors()}")
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

# Dashboard API Data
@app.get("/api/jobs/check-title")
async def api_check_title(title: str, exclude: str = None, channel_id: int = None, user: str = Depends(get_current_user)):
    """
    Verifica si ya existe un job con este título exacto.
    Usado por n8n ANTES de crear un nuevo Short para evitar duplicados y ahorrar créditos.
    Responde con {"exists": true/false, "title": "..."}.
    Si exists=true, n8n debe buscar una historia diferente.
    """
    exists = db.check_title_exists(title, exclude_job_id=exclude, channel_id=channel_id)
    return {
        "exists": exists,
        "title": title,
        "message": "El título ya existe. Busca otra historia viral." if exists else "Título disponible. Puedes crear el Short."
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
        raise HTTPException(status_code=400, detail="ID de candidato inválido")

@app.post("/api/candidates/{cand_id}/process")
async def api_process_candidate(cand_id: int, request: Request, background_tasks: BackgroundTasks):
    """
    Toma un candidato, genera un storyboard con IA (traducción, escenas, prompts),
    reutiliza assets de galería si existen, y crea un borrador en el engine.
    """
    await get_current_user(request)
    candidate = db.get_candidate_by_id(cand_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidato no encontrado")

    try:
        # 1. Generar Storyboard IA (Simula lo que hacía n8n)
        logger.info(f"Generando storyboard IA para candidato {cand_id}: {candidate['title']}")
        storyboard_data = ai_manager.generate_storyboard(candidate["content"])
        scenes_raw = storyboard_data.get("scenes", [])
        
        if not scenes_raw:
            # Fallback si la IA falla o el JSON es vacío
            scenes_raw = [{"text": candidate["content"][:500], "image_prompt": "cinematic background", "subtitle_pos": 8, "subtitle_size": 48}]

        # 2. Gallery-First & Preparar escenas finales
        final_scenes = []
        batch_scenes_to_gen = []
        
        for scene in scenes_raw:
            prompt = scene.get("image_prompt", "")
            niche = candidate.get("niche", "default")
            
            # Buscar coincidencia exacta en galería
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
            "message": "✅ Storyboard e imágenes (borrador) listos en el engine."
        }
        
    except Exception as e:
        logger.error(f"Error en api_process_candidate: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en la generación IA: {str(e)}")

@app.get("/api/jobs")
async def api_get_jobs(page: int = 1, limit: int = 25, search: str = None, channel_id: int = None, user: str = Depends(get_current_user)):
    offset = (page - 1) * limit
    jobs = db.get_recent_jobs(limit=limit, offset=offset, search=search, channel_id=channel_id)
    total = db.count_jobs(search=search, channel_id=channel_id)
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
    TODOS los que no existen aún en la BD, preservando todos sus campos originales.
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
    Guarda lista de candidatos en el cache temporal (12h) para la selección manual vía Telegram.
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
    Recupera un candidato específico por session_id e índice (0-based).
    Usado por el workflow Telegram Handler cuando el usuario toca un botón.
    """
    session = _candidates_cache.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión expirada o no encontrada. Ejecuta el workflow de nuevo.")
    if session["expires_at"] < datetime.now():
        del _candidates_cache[session_id]
        raise HTTPException(status_code=404, detail="Sesión expirada (>12h). Ejecuta el workflow de nuevo.")
    items = session["items"]
    if index < 0 or index >= len(items):
        raise HTTPException(status_code=400, detail=f"Índice {index} fuera de rango (0-{len(items)-1})")
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
    NON_SENSITIVE_KEYS = {"2FA_ENABLED", "DEFAULT_TTS_ENGINE", "DEFAULT_VOICE_ID", "DEFAULT_TTS_SPEED", "DEFAULT_MUSIC_FILENAME"}
    
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
            
    return settings

class SaveSettingsRequest(BaseModel):
    provider: str
    api_key: str

@app.post("/api/settings")
async def api_save_settings(req: SaveSettingsRequest, user: str = Depends(get_current_user)):
    # Lógica inteligente para el nombre de la llave
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
        db.add_ai_task(task_id, req.prompt, req.niche, req.model or "seedream/5-lite-text-to-image")
        
        # Start polling in background
        background_tasks.add_task(process_ai_task_background, task_id, api_key, req)
        
        return {"status": "processing", "task_id": task_id}
    except Exception as e:
        logger.error(f"Ai Generate Start Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ai/tasks")
async def api_get_ai_tasks(page: int = 1, limit: int = 25, search: str = None, user: str = Depends(get_current_user)):
    offset = (page - 1) * limit
    tasks = db.get_ai_tasks(limit=limit, offset=offset, search=search)
    total = db.count_ai_tasks(search=search)
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
        raise HTTPException(status_code=400, detail="No hay llaves de API válidas o con saldo.")
        
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
                # Usamos add_ai_task y lo forzamos a draft después porque add_ai_task inserta como 'processing'
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
        # Petición a la API
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
    # pero aquí confiamos en la lista de tasks de la DB)
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
        raise HTTPException(status_code=404, detail="No se encontró o no se pudo borrar la tarea.")
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
                        result = kie_manager._process_completed_image(image_url, req.prompt, req.niche, req.model)
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
    
    # ---- Verificación de título único ANTES de renderizar ----
    if req.title and db.check_title_exists(req.title, exclude_job_id=req.job_id, channel_id=req.channel_id):
        raise HTTPException(
            status_code=409,
            detail=f"TITULO_DUPLICADO: Ya existe un trabajo con el título '{req.title}'. Busca otra historia viral."
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

    job_to_overwrite = None
    if req.job_id:
        existing = db.get_job(req.job_id)
        if existing:
            db.delete_job(req.job_id) # remove old to create new or just modify? simpler to recreate
            job_to_overwrite = req.job_id
            
    job_id = job_to_overwrite if job_to_overwrite else f"cinema_{str(uuid.uuid4())[:8]}"
    
    import json
    scenes_json = json.dumps([s.dict() for s in req.scenes])
    
    db.add_job(
        job_id, 
        f"Storyboard: {len(req.scenes)} escenas", 
        req.niche, 
        req.voice_id, 
        scenes_json=scenes_json, 
        music_filename=req.music_filename,
        tts_engine=req.tts_engine,
        tts_speed=req.tts_speed,
        title=req.title,
        channel_id=req.channel_id
    )
    
    # Run in background as it takes time (Cloudinary + FFmpeg)
    background_tasks.add_task(process_storyboard_job, job_id, req)
    return {"status": "processing", "job_id": job_id}

@app.post("/api/storyboard/draft")
async def api_draft_storyboard(req: StoryboardRequest, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    """Guarda un Short como borrador sin generar vídeos automáticamente."""

    # ---- Verificación de título único ANTES de crear el borrador ----
    if req.title and db.check_title_exists(req.title, exclude_job_id=req.job_id, channel_id=req.channel_id):
        raise HTTPException(
            status_code=409,
            detail=f"TITULO_DUPLICADO: Ya existe un trabajo con el título '{req.title}'. Busca otra historia viral."
        )

    job_to_overwrite = None
    if req.job_id:
        existing = db.get_job(req.job_id)
        if existing:
            db.delete_job(req.job_id)
            job_to_overwrite = req.job_id
            
    job_id = job_to_overwrite if job_to_overwrite else f"cinema_{str(uuid.uuid4())[:8]}"
    
    import json
    scenes_json = json.dumps([s.dict() for s in req.scenes])
    
    db.add_job(
        job_id, 
        f"Storyboard: {len(req.scenes)} escenas", 
        req.niche, 
        req.voice_id, 
        status="draft", # Cambio clave
        scenes_json=scenes_json, 
        music_filename=req.music_filename or db.get_setting("DEFAULT_MUSIC_FILENAME"),
        tts_engine=req.tts_engine or db.get_setting("DEFAULT_TTS_ENGINE") or "edge-tts",
        tts_speed=req.tts_speed or float(db.get_setting("DEFAULT_TTS_SPEED") or 1.0),
        title=req.title,
        channel_id=req.channel_id
    )
    
    return {"status": "draft", "job_id": job_id, "title": req.title}

class JobStatusRequest(BaseModel):
    status: str

@app.post("/api/jobs/{job_id}/status")
async def api_update_job_status(job_id: str, req: JobStatusRequest, user: str = Depends(get_current_user)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        
    db.update_job_status(job_id, req.status)
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

def resolve_background_video(niche: str, bg_name: str, custom_id: str = None, channel_id: int = None) -> str:
    """Resuelve la ruta definitiva del vídeo o imagen de fondo."""
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
            
    # 2. Buscar en la galería del canal y, si no aparece, en la galería global
    if bg_name:
        for gallery in iter_galleries():
            for media in gallery:
                # Primero intentar coincidencia exacta con el nombre de archivo (más seguro)
                if media["filename"] == bg_name or media["original_name"] == bg_name:
                    path = os.path.join(UPLOAD_DIR, media["filename"])
                    if os.path.exists(path):
                        return path
                    
    # 3. Buscar en la carpeta backgrounds física por nicho
    if niche and bg_name:
        niche_path = os.path.join(BASE_DIR, "backgrounds", niche, bg_name)
        if os.path.exists(niche_path):
            return niche_path
            
    # 4. Fallback: Buscar CUALQUIER vídeo en la galería marcados con ese nicho o en general
    for gallery in iter_galleries():
        filtered = [m for m in gallery if m.get("file_type") == "video"]
        if filtered:
            import random
            chosen = random.choice(filtered)
            path = os.path.join(UPLOAD_DIR, chosen["filename"])
            if os.path.exists(path):
                return path

    # 5. Fallbacks estáticos
    fallbacks = [
        os.path.join(BASE_DIR, "backgrounds", "default", "default.mp4"),
        os.path.join(BASE_DIR, "backgrounds", "default.mp4"),
        os.path.join(BASE_DIR, "backgrounds", "terror", "default.mp4"),
        os.path.join(BASE_DIR, "backgrounds", "curiosidades", "default.mp4")
    ]
    for fallback in fallbacks:
        if os.path.exists(fallback):
            return fallback

    # 6. Último recurso: El primer vídeo que encontremos en la galería
    for gallery in iter_galleries():
        for media in gallery:
            if media["file_type"] == "video":
                path = os.path.join(UPLOAD_DIR, media["filename"])
                if os.path.exists(path):
                    return path
            
    return None

async def process_storyboard_job(job_id, req: StoryboardRequest):
    try:
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
                raise ValueError(f"No se pudo encontrar ningún vídeo de fondo en el sistema para la escena {idx+1}. Por favor, sube al menos un vídeo a la galería.")

            scene_clips.append({
                "audio": audio_path,
                "video": bg_path,
                "text": scene.text,
                "sub_pos": scene.subtitle_pos,
                "sub_size": scene.subtitle_size
            })

        # 3. Final Assembly
        import time
        version = int(time.time())
        output_filename = f"{job_id}_v{version}.mp4"
        output_path = os.path.join(BASE_DIR, "shorts", output_filename)
        
        final_video = video_editor.assemble_storyboard(
            scene_clips, 
            output_path, 
            music_path=global_music_path
        )
        
        db.update_job_status(job_id, "rendered", video_url=f"/static/shorts/{output_filename}")
        logger.info(f"Cinema Storyboard {job_id} renderizado. Esperando aprobación humana.")

    except Exception as e:
        logger.error(f"Storyboard Render Failed: {str(e)}")
        db.update_job_status(job_id, "failed", error_message=str(e))

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
    db.add_job(job_id, request.text, request.niche, request.voice_id, channel_id=request.channel_id)

    # Background Selection Logic (Unified)
    bg_path = resolve_background_video(request.niche, request.background_video_name, request.custom_background_filename, channel_id=request.channel_id)

    music_path = os.path.join(UPLOAD_DIR, request.music_filename) if request.music_filename else None
    logo_path = os.path.join(UPLOAD_DIR, request.logo_filename) if request.logo_filename else None

    if not bg_path or not os.path.exists(bg_path):
        db.update_job_status(job_id, "failed", error_message="Fondo no encontrado")
        raise HTTPException(status_code=400, detail="Error: No hay vídeos de fondo. Sube un vídeo a la galería o añade un 'default.mp4' en storage/backgrounds/default/")

    try:
        # 1. Generate Speech
        tts_manager.text_to_speech(request.text, voice_id=request.voice_id, output_path=audio_path)
        
        # 2. Render Video (FFmpeg) with extra params
        video_editor.create_short(
            bg_path, audio_path, output_path,
            music_path=music_path, music_volume=request.music_volume,
            logo_path=logo_path, logo_position=request.logo_position
        )
        
        # Static URL for n8n to download
        download_url = f"/static/shorts/{output_filename}"

        # Update DB success
        db.update_job_status(job_id, "completed", video_url=download_url)

        return {
            "status": "success",
            "job_id": job_id,
            "video_url": download_url,
            "local_path": output_path
        }

    except Exception as e:
        logger.error(f"Render failed: {e}")
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
