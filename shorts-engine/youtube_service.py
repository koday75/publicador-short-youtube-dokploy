import base64
import hashlib
import json
import logging
import os
import secrets
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class YouTubeAuthError(Exception):
    pass


class YouTubeChannelService:
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
    USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
    YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self, db):
        self.db = db
        self._fernet = Fernet(self._resolve_fernet_key())
        self.reload_oauth_config()

    def _get_runtime_setting(self, key: str, default: str = "") -> str:
        value = self.db.get_setting(key)
        if value and str(value).strip():
            return str(value).strip()
        return os.getenv(key, default).strip()

    def reload_oauth_config(self):
        self.fallback_client_id = self._get_runtime_setting("GOOGLE_CLIENT_ID")
        self.fallback_client_secret = self._get_runtime_setting("GOOGLE_CLIENT_SECRET")
        self.fallback_redirect_uri = self._get_runtime_setting("GOOGLE_REDIRECT_URI")
        self.scopes = self._resolve_scopes()
        return self

    def _resolve_channel_oauth_config(
        self,
        channel_id: int,
        explicit_redirect_uri: str | None = None,
        prefer_explicit_redirect: bool = False,
    ) -> dict[str, Any]:
        channel = self.db.get_youtube_channel(channel_id)
        if not channel:
            raise YouTubeAuthError("Canal no encontrado.")

        client_id = (channel.get("google_client_id") or self.fallback_client_id or "").strip()
        client_secret = (channel.get("google_client_secret") or self.fallback_client_secret or "").strip()
        if prefer_explicit_redirect:
            redirect_uri = (explicit_redirect_uri or channel.get("google_redirect_uri") or self.fallback_redirect_uri or "").strip()
        else:
            redirect_uri = (channel.get("google_redirect_uri") or explicit_redirect_uri or self.fallback_redirect_uri or "").strip()

        if not client_id or not client_secret:
            raise YouTubeAuthError(
                "Este canal no tiene credenciales OAuth de Google configuradas. "
                "Completa Google Client ID y Google Client Secret en el formulario del canal."
            )
        if not redirect_uri:
            raise YouTubeAuthError(
                "No se pudo determinar la redirect_uri de OAuth para este canal."
            )

        return {
            "channel": channel,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scopes": self.scopes,
        }

    def _resolve_scopes(self) -> list[str]:
        raw_scopes = os.getenv("YOUTUBE_SCOPES")
        scopes = []
        if raw_scopes:
            scopes.extend([scope.strip() for scope in raw_scopes.split() if scope.strip()])
        else:
            scopes.extend([
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.force-ssl",
                "https://www.googleapis.com/auth/youtube.readonly",
                "openid",
                "email",
                "profile",
            ])

        for scope in [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/youtube.readonly",
            "openid",
            "email",
            "profile",
        ]:
            if scope not in scopes:
                scopes.append(scope)
        return scopes

    def _ensure_required_scopes(self, channel_id: int, required_scopes: list[str]):
        channel = self.db.get_youtube_channel(channel_id)
        granted = (channel or {}).get("scopes_granted") or ""
        granted_set = {scope.strip() for scope in granted.split() if scope.strip()}
        missing = [scope for scope in required_scopes if scope not in granted_set]
        if missing:
            friendly = ", ".join(scope.rsplit("/", 1)[-1] for scope in missing)
            raise YouTubeAuthError(
                "Este canal se autorizó antes de que la app pidiera permisos de edición de YouTube. "
                f"Vuelve a conectar el canal para conceder: {friendly}."
            )

    def _resolve_fernet_key(self) -> bytes:
        raw_key = os.getenv("YOUTUBE_TOKEN_ENCRYPTION_KEY", "").strip()
        if raw_key:
            if len(raw_key) == 44:
                try:
                    Fernet(raw_key.encode("utf-8"))
                    return raw_key.encode("utf-8")
                except Exception:
                    pass
            digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
            return base64.urlsafe_b64encode(digest)

        seed = "|".join(
            [
                os.getenv("GOOGLE_CLIENT_ID", ""),
                os.getenv("GOOGLE_CLIENT_SECRET", ""),
                os.getenv("DASHBOARD_PASSWORD", "shorts-engine"),
            ]
        )
        logger.warning(
            "YOUTUBE_TOKEN_ENCRYPTION_KEY no está configurada. "
            "Se usará una clave derivada; en producción conviene definir una fija."
        )
        return base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())

    def encrypt_token(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt_token(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise YouTubeAuthError("No se pudo descifrar un token guardado.") from exc

    def _build_auth_url(self, state: str, client_id: str, redirect_uri: str, prompt: str = "consent") -> str:
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": prompt,
            "state": state,
        }
        from urllib.parse import urlencode

        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _to_iso(self, dt: Optional[datetime]) -> Optional[str]:
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def _parse_dt(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    def _store_connection_result(self, channel_id: int, token_payload: dict[str, Any], profile: dict[str, Any], channel_info: dict[str, Any]):
        channel_url = f"https://www.youtube.com/channel/{channel_info.get('id')}" if channel_info.get("id") else None
        custom_url = channel_info.get("snippet", {}).get("customUrl")
        if custom_url:
            if custom_url.startswith("@") or custom_url.startswith("http"):
                channel_handle = custom_url
            else:
                channel_handle = f"@{custom_url}"
        else:
            channel_handle = None

        expires_in = int(token_payload.get("expires_in") or 3600)
        token_expires_at = self._now() + timedelta(seconds=expires_in)
        existing = self.db.get_youtube_channel(channel_id)

        refresh_token = token_payload.get("refresh_token")
        if not refresh_token and existing:
            refresh_token = self.decrypt_token(existing.get("refresh_token_encrypted"))

        update_payload = {
            "youtube_channel_id": channel_info.get("id"),
            "youtube_channel_title": channel_info.get("snippet", {}).get("title"),
            "youtube_channel_handle": channel_handle,
            "youtube_channel_url": channel_url,
            "thumbnail_url": self._extract_thumbnail(channel_info),
            "subscriber_count": self._parse_int(channel_info.get("statistics", {}).get("subscriberCount")),
            "view_count": self._parse_int(channel_info.get("statistics", {}).get("viewCount")),
            "video_count": self._parse_int(channel_info.get("statistics", {}).get("videoCount")),
            "connected_google_email": profile.get("email"),
            "scopes_granted": token_payload.get("scope") or " ".join(self.scopes),
            "access_token_encrypted": self.encrypt_token(token_payload.get("access_token")),
            "refresh_token_encrypted": self.encrypt_token(refresh_token),
            "token_expires_at": self._to_iso(token_expires_at),
            "connection_status": "connected",
            "status": "active" if existing and existing.get("status") == "active" else (existing.get("status") if existing else "inactive"),
            "last_connection_error": None,
        }
        self.db.update_youtube_channel(channel_id, update_payload)
        return self.db.get_youtube_channel(channel_id)

    def _extract_thumbnail(self, channel_info: dict[str, Any]) -> Optional[str]:
        snippet = channel_info.get("snippet", {})
        thumbnails = snippet.get("thumbnails") or {}
        for key in ["high", "medium", "default", "standard"]:
            if thumbnails.get(key, {}).get("url"):
                return thumbnails[key]["url"]
        return None

    def _parse_int(self, value: Any) -> Optional[int]:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except Exception:
            return None

    def _coerce_bool(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return default

    def generate_auth_url(self, channel_id: int, redirect_uri: str | None = None, force_consent: bool = True) -> dict[str, Any]:
        self.reload_oauth_config()
        state = secrets.token_urlsafe(32)
        expires_at = self._now() + timedelta(minutes=15)
        oauth_config = self._resolve_channel_oauth_config(channel_id, explicit_redirect_uri=redirect_uri)
        self.db.create_oauth_state(state, channel_id, expires_at, oauth_config["redirect_uri"])
        prompt = "consent" if force_consent else "select_account"
        return {
            "auth_url": self._build_auth_url(
                state=state,
                client_id=oauth_config["client_id"],
                redirect_uri=oauth_config["redirect_uri"],
                prompt=prompt,
            ),
            "state": state,
            "expires_at": self._to_iso(expires_at),
            "redirect_uri": oauth_config["redirect_uri"],
        }

    def exchange_code_for_tokens(self, code: str, channel_id: int, redirect_uri: str | None = None) -> dict[str, Any]:
        self.reload_oauth_config()
        oauth_config = self._resolve_channel_oauth_config(channel_id, explicit_redirect_uri=redirect_uri, prefer_explicit_redirect=True)

        payload = {
            "code": code,
            "client_id": oauth_config["client_id"],
            "client_secret": oauth_config["client_secret"],
            "redirect_uri": oauth_config["redirect_uri"],
            "grant_type": "authorization_code",
        }
        res = requests.post(self.TOKEN_ENDPOINT, data=payload, timeout=30)
        try:
            token_data = res.json()
        except Exception:
            token_data = {"error": res.text}
        if not res.ok:
            raise YouTubeAuthError(f"No se pudo intercambiar el code por tokens: {token_data}")
        if token_data.get("error"):
            raise YouTubeAuthError(f"No se pudo intercambiar el code por tokens: {token_data}")

        return {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "scope": token_data.get("scope") or " ".join(self.scopes),
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_in": int(token_data.get("expires_in") or 3600),
        }

    def handle_oauth_callback(self, code: str, state: str) -> dict[str, Any]:
        self.reload_oauth_config()
        pending = self.db.consume_oauth_state(state)
        if not pending:
            raise YouTubeAuthError("El estado OAuth no es válido o ya expiró.")

        expires_at = self._parse_dt(pending.get("expires_at"))
        if expires_at and expires_at < self._now():
            raise YouTubeAuthError("La autorización OAuth expiró antes de completarse.")

        channel_id = int(pending["channel_id"])
        token_payload = self.exchange_code_for_tokens(code, channel_id, redirect_uri=pending.get("redirect_uri"))
        access_token = token_payload.get("access_token")
        if not access_token:
            raise YouTubeAuthError("Google no devolvió access_token.")

        channel_info = self.get_authenticated_channel(access_token)
        profile = self.get_google_profile(access_token)
        updated = self._store_connection_result(channel_id, token_payload, profile, channel_info)
        return updated

    def get_google_profile(self, access_token: str) -> dict[str, Any]:
        try:
            res = requests.get(
                self.USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            if not res.ok:
                return {}
            return res.json()
        except Exception:
            return {}

    def get_authenticated_channel(self, access_token: str) -> dict[str, Any]:
        self.reload_oauth_config()
        params = {
            "part": "snippet,statistics",
            "mine": "true",
            "maxResults": 1,
            "fields": "items(id,snippet(title,customUrl,thumbnails),statistics(subscriberCount,viewCount,videoCount))",
            "key": os.getenv("GOOGLE_API_KEY", "").strip() or None,
        }
        params = {k: v for k, v in params.items() if v is not None}
        res = requests.get(
            f"{self.YOUTUBE_API_BASE}/channels",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if not res.ok:
            try:
                detail = res.json()
            except Exception:
                detail = {"error": res.text}
            raise YouTubeAuthError(f"No se pudo leer el canal de YouTube: {detail}")

        data = res.json()
        items = data.get("items") or []
        if not items:
            raise YouTubeAuthError("YouTube no devolvió canales para esta cuenta.")
        return items[0]

    def _load_channel_credentials(self, channel_id: int, require_access_token: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        channel = self.db.get_youtube_channel(channel_id)
        if not channel:
            raise YouTubeAuthError("Canal no encontrado.")

        access_token = self.decrypt_token(channel.get("access_token_encrypted"))
        refresh_token = self.decrypt_token(channel.get("refresh_token_encrypted"))
        if not refresh_token:
            raise YouTubeAuthError("El canal no tiene tokens OAuth válidos.")
        if require_access_token and not access_token:
            raise YouTubeAuthError("El canal no tiene tokens OAuth válidos.")

        scopes = channel.get("scopes_granted")
        if isinstance(scopes, str) and scopes.strip():
            scope_list = scopes.split()
        else:
            scope_list = self.scopes
        expires_at = self._parse_dt(channel.get("token_expires_at"))
        return channel, {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expiry": expires_at,
            "scopes": scope_list,
        }

    def refresh_access_token(self, channel_id: int) -> dict[str, Any]:
        self.reload_oauth_config()
        oauth_config = self._resolve_channel_oauth_config(channel_id)
        channel, token_info = self._load_channel_credentials(channel_id, require_access_token=False)
        refresh_token = token_info.get("refresh_token")
        if not refresh_token:
            raise YouTubeAuthError("No hay refresh_token disponible para renovar la sesión.")

        try:
            res = requests.post(
                self.TOKEN_ENDPOINT,
                data={
                    "client_id": oauth_config["client_id"],
                    "client_secret": oauth_config["client_secret"],
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
            token_data = res.json()
            if not res.ok or token_data.get("error"):
                raise YouTubeAuthError(f"No se pudo renovar el token: {token_data}")
        except Exception as exc:
            self.db.update_youtube_channel(
                channel_id,
                {
                    "connection_status": "expired",
                    "last_connection_error": f"No se pudo renovar el token: {exc.__class__.__name__}",
                },
            )
            raise YouTubeAuthError("No se pudo renovar el access token.") from exc

        access_token = token_data.get("access_token")
        new_refresh = token_data.get("refresh_token") or refresh_token
        expires_in = int(token_data.get("expires_in") or 3600)
        token_expires_at = self._now() + timedelta(seconds=expires_in)
        update_payload = {
            "access_token_encrypted": self.encrypt_token(access_token),
            "refresh_token_encrypted": self.encrypt_token(new_refresh),
            "token_expires_at": self._to_iso(token_expires_at),
            "connection_status": "connected",
            "last_connection_error": None,
        }
        self.db.update_youtube_channel(channel_id, update_payload)
        updated = self.db.get_youtube_channel(channel_id)
        return {
            "channel": updated,
            "access_token": access_token,
            "credentials": {
                "token": access_token,
                "refresh_token": new_refresh,
                "expiry": token_expires_at,
                "scopes": token_info.get("scopes", self.scopes),
            },
            "refreshed": True,
        }

    def get_authorized_client(self, channel_id: int) -> dict[str, Any]:
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        channel, token_info = self._load_channel_credentials(channel_id)
        expires_at = token_info.get("expiry")
        needs_refresh = (
            not token_info.get("access_token")
            or not expires_at
            or expires_at <= self._now() + timedelta(seconds=60)
        )

        refreshed = False
        if needs_refresh:
            refreshed_result = self.refresh_access_token(channel_id)
            token_info = refreshed_result["credentials"]
            channel = refreshed_result["channel"]
            refreshed = True

        return {
            "channel": channel,
            "credentials": token_info,
            "access_token": token_info.get("token") or token_info.get("access_token"),
            "refreshed": refreshed,
        }

    def test_connection(self, channel_id: int) -> dict[str, Any]:
        self.reload_oauth_config()
        try:
            self._resolve_channel_oauth_config(channel_id)
            auth = self.get_authorized_client(channel_id)
            access_token = auth["access_token"]
            channel_info = self.get_authenticated_channel(access_token)
            profile = self.get_google_profile(access_token)
            updated = self._store_connection_result(
                channel_id,
                {
                    "access_token": access_token,
                    "refresh_token": auth["credentials"].get("refresh_token"),
                    "scope": " ".join(auth["credentials"].get("scopes") or self.scopes),
                    "expires_in": int((auth["credentials"].get("expiry") - self._now()).total_seconds()) if auth["credentials"].get("expiry") else 3600,
                },
                profile,
                channel_info,
            )
            self.db.update_youtube_channel(
                channel_id,
                {
                    "last_connection_test_at": self._to_iso(self._now()),
                    "last_connection_error": None,
                    "connection_status": "connected",
                },
            )
            return {"ok": True, "refreshed": auth["refreshed"], "channel": updated}
        except YouTubeAuthError as exc:
            self.db.update_youtube_channel(
                channel_id,
                {
                    "last_connection_test_at": self._to_iso(self._now()),
                    "last_connection_error": str(exc),
                    "connection_status": "error",
                },
            )
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            self.db.update_youtube_channel(
                channel_id,
                {
                    "last_connection_test_at": self._to_iso(self._now()),
                    "last_connection_error": str(exc),
                    "connection_status": "error",
                },
            )
            return {"ok": False, "error": "No se pudo verificar la conexión."}

    def refresh_channel_snapshot(self, channel_id: int) -> dict[str, Any]:
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]
        channel_info = self.get_authenticated_channel(access_token)
        profile = self.get_google_profile(access_token)
        return self._store_connection_result(
            channel_id,
            {
                "access_token": access_token,
                "refresh_token": auth["credentials"].get("refresh_token"),
                "scope": " ".join(auth["credentials"].get("scopes") or self.scopes),
                "expires_in": int((auth["credentials"].get("expiry") - self._now()).total_seconds()) if auth["credentials"].get("expiry") else 3600,
            },
            profile,
            channel_info,
        )

    def revoke_connection(self, channel_id: int) -> dict[str, Any]:
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        channel = self.db.get_youtube_channel(channel_id)
        if not channel:
            raise YouTubeAuthError("Canal no encontrado.")

        token = self.decrypt_token(channel.get("refresh_token_encrypted")) or self.decrypt_token(channel.get("access_token_encrypted"))
        if token:
            try:
                requests.post(self.REVOKE_ENDPOINT, params={"token": token}, timeout=30)
            except Exception:
                pass

        self.db.update_youtube_channel(
            channel_id,
            {
                "youtube_channel_id": None,
                "youtube_channel_title": None,
                "youtube_channel_handle": None,
                "youtube_channel_url": None,
                "thumbnail_url": None,
                "subscriber_count": None,
                "connected_google_email": None,
                "scopes_granted": None,
                "access_token_encrypted": None,
                "refresh_token_encrypted": None,
                "token_expires_at": None,
                "connection_status": "revoked",
                "last_connection_error": None,
            },
        )
        return {"ok": True}

    def upload_video(self, channel_id: int, file_path_or_stream, metadata: dict[str, Any]):
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]
        snippet = {
            "title": metadata["title"],
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags") or [],
            "categoryId": str(metadata.get("categoryId", "22")),
        }
        status = {
            "privacyStatus": metadata.get("privacyStatus", "private"),
            "license": metadata.get("license", "youtube"),
            "embeddable": self._coerce_bool(metadata.get("embeddable"), True),
            "publicStatsViewable": self._coerce_bool(metadata.get("publicStatsViewable"), True),
            "selfDeclaredMadeForKids": self._coerce_bool(metadata.get("selfDeclaredMadeForKids"), False),
            "containsSyntheticMedia": self._coerce_bool(metadata.get("containsSyntheticMedia"), False),
        }
        default_language = metadata.get("defaultLanguage")
        if default_language:
            snippet["defaultLanguage"] = default_language
        publish_at = metadata.get("publishAt")
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at
        mime_type = metadata.get("mimeType")
        if not mime_type:
            if isinstance(file_path_or_stream, str):
                mime_type, _ = mimetypes.guess_type(file_path_or_stream)
            mime_type = mime_type or "video/mp4"

        if hasattr(file_path_or_stream, "read"):
            stream = file_path_or_stream
            if hasattr(stream, "seek"):
                try:
                    stream.seek(0)
                except Exception:
                    pass
            file_bytes = stream.read()
        else:
            with open(file_path_or_stream, "rb") as fh:
                file_bytes = fh.read()

        init_res = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": str(self._coerce_bool(metadata.get("notifySubscribers"), True)).lower(),
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
            },
            data=json.dumps({"snippet": snippet, "status": status}),
            timeout=60,
        )
        if not init_res.ok:
            raise YouTubeAuthError(f"No se pudo iniciar la subida: {init_res.text}")

        upload_url = init_res.headers.get("Location")
        if not upload_url:
            raise YouTubeAuthError("YouTube no devolvió la URL de subida resumable.")

        upload_res = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": mime_type,
                "Content-Length": str(len(file_bytes)),
            },
            data=file_bytes,
            timeout=600,
        )
        if not upload_res.ok:
            raise YouTubeAuthError(f"No se pudo completar la subida: {upload_res.text}")
        return upload_res.json()

    def set_thumbnail(self, channel_id: int, video_id: str, file_path_or_stream):
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        self._ensure_required_scopes(channel_id, ["https://www.googleapis.com/auth/youtube.force-ssl"])
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]

        if hasattr(file_path_or_stream, "read"):
            stream = file_path_or_stream
            if hasattr(stream, "seek"):
                try:
                    stream.seek(0)
                except Exception:
                    pass
            file_bytes = stream.read()
            mime_type = getattr(stream, "content_type", None) or "image/jpeg"
        else:
            with open(file_path_or_stream, "rb") as fh:
                file_bytes = fh.read()
            mime_type, _ = mimetypes.guess_type(str(file_path_or_stream))
            mime_type = mime_type or "image/jpeg"

        res = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            params={"videoId": video_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": mime_type,
                "Content-Length": str(len(file_bytes)),
            },
            data=file_bytes,
            timeout=120,
        )
        if not res.ok:
            raise YouTubeAuthError(f"No se pudo asignar la miniatura: {res.text}")
        return res.json()

    def update_video_metadata(self, channel_id: int, video_id: str, metadata: dict[str, Any]):
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        self._ensure_required_scopes(channel_id, ["https://www.googleapis.com/auth/youtube.force-ssl"])
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]

        snippet = {
            "title": metadata["title"],
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags") or [],
            "categoryId": str(metadata.get("categoryId", "22")),
        }
        default_language = metadata.get("defaultLanguage")
        if default_language:
            snippet["defaultLanguage"] = default_language

        status = {
            "privacyStatus": metadata.get("privacyStatus", "private"),
            "license": metadata.get("license", "youtube"),
            "embeddable": self._coerce_bool(metadata.get("embeddable"), True),
            "publicStatsViewable": self._coerce_bool(metadata.get("publicStatsViewable"), True),
            "selfDeclaredMadeForKids": self._coerce_bool(metadata.get("selfDeclaredMadeForKids"), False),
            "containsSyntheticMedia": self._coerce_bool(metadata.get("containsSyntheticMedia"), False),
        }
        publish_at = metadata.get("publishAt")
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at

        res = requests.put(
            f"{self.YOUTUBE_API_BASE}/videos",
            params={"part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            data=json.dumps({"id": video_id, "snippet": snippet, "status": status}),
            timeout=60,
        )
        if not res.ok:
            raise YouTubeAuthError(f"No se pudo actualizar el vídeo: {res.text}")
        return res.json()

    def delete_video(self, channel_id: int, video_id: str):
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        self._ensure_required_scopes(channel_id, ["https://www.googleapis.com/auth/youtube.force-ssl"])
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]

        res = requests.delete(
            f"{self.YOUTUBE_API_BASE}/videos",
            params={"id": video_id},
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            timeout=60,
        )
        if res.status_code not in {200, 204}:
            raise YouTubeAuthError(f"No se pudo eliminar el vídeo: {res.text}")
        return {"deleted": True, "video_id": video_id}

    def get_video_statistics(self, channel_id: int, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]
        ids = [str(video_id).strip() for video_id in video_ids if str(video_id).strip()]
        if not ids:
            return {}

        stats_map: dict[str, dict[str, Any]] = {}
        for idx in range(0, len(ids), 50):
            chunk = ids[idx:idx + 50]
            res = requests.get(
                f"{self.YOUTUBE_API_BASE}/videos",
                params={
                    "part": "statistics",
                    "id": ",".join(chunk),
                    "fields": "items(id,statistics(viewCount,likeCount,commentCount))",
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            if not res.ok:
                raise YouTubeAuthError(f"No se pudieron leer las estadísticas del vídeo: {res.text}")

            payload = res.json()
            for item in payload.get("items") or []:
                stats = item.get("statistics") or {}
                stats_map[item.get("id")] = {
                    "view_count": self._parse_int(stats.get("viewCount")),
                    "like_count": self._parse_int(stats.get("likeCount")),
                    "comment_count": self._parse_int(stats.get("commentCount")),
                }
        return stats_map

    def list_video_comments(self, channel_id: int, video_id: str, max_results: int = 20, page_token: Optional[str] = None):
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        self._ensure_required_scopes(channel_id, ["https://www.googleapis.com/auth/youtube.force-ssl"])
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]

        params = {
            "part": "snippet,replies",
            "videoId": video_id,
            "maxResults": max(1, min(int(max_results or 20), 100)),
            "textFormat": "plainText",
            "order": "time",
        }
        if page_token:
            params["pageToken"] = page_token

        res = requests.get(
            f"{self.YOUTUBE_API_BASE}/commentThreads",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=60,
        )
        if not res.ok:
            raise YouTubeAuthError(f"No se pudieron leer los comentarios del vídeo: {res.text}")

        payload = res.json()
        items: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}
            top_comment = (snippet.get("topLevelComment") or {}).get("snippet") or {}
            replies = []
            for reply in ((item.get("replies") or {}).get("comments") or []):
                reply_snippet = reply.get("snippet") or {}
                replies.append({
                    "comment_id": reply.get("id"),
                    "text": reply_snippet.get("textDisplay") or reply_snippet.get("textOriginal") or "",
                    "author_name": (reply_snippet.get("authorDisplayName") or "").strip(),
                    "author_channel_id": reply_snippet.get("authorChannelId", {}).get("value"),
                    "published_at": reply_snippet.get("publishedAt"),
                    "updated_at": reply_snippet.get("updatedAt"),
                    "like_count": self._parse_int(reply_snippet.get("likeCount")),
                    "is_reply": True,
                })
            items.append({
                "thread_id": item.get("id"),
                "comment_id": (snippet.get("topLevelComment") or {}).get("id"),
                "video_id": snippet.get("videoId") or video_id,
                "channel_id": snippet.get("channelId"),
                "author_name": (top_comment.get("authorDisplayName") or "").strip(),
                "author_channel_id": top_comment.get("authorChannelId", {}).get("value"),
                "text": top_comment.get("textDisplay") or top_comment.get("textOriginal") or "",
                "published_at": top_comment.get("publishedAt"),
                "updated_at": top_comment.get("updatedAt"),
                "like_count": self._parse_int(top_comment.get("likeCount")),
                "reply_count": self._parse_int(snippet.get("totalReplyCount")),
                "can_reply": bool(snippet.get("canReply", True)),
                "replies": replies,
                "raw": item,
            })

        return {
            "items": items,
            "next_page_token": payload.get("nextPageToken"),
            "page_info": payload.get("pageInfo") or {},
        }

    def reply_to_comment(self, channel_id: int, parent_comment_id: str, text: str):
        self.reload_oauth_config()
        self._resolve_channel_oauth_config(channel_id)
        self._ensure_required_scopes(channel_id, ["https://www.googleapis.com/auth/youtube.force-ssl"])
        auth = self.get_authorized_client(channel_id)
        access_token = auth["access_token"]
        reply_text = (text or "").strip()
        if not reply_text:
            raise YouTubeAuthError("La respuesta no puede estar vacía.")

        res = requests.post(
            f"{self.YOUTUBE_API_BASE}/comments",
            params={"part": "snippet"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            data=json.dumps({
                "snippet": {
                    "parentId": parent_comment_id,
                    "textOriginal": reply_text,
                }
            }),
            timeout=60,
        )
        if not res.ok:
            raise YouTubeAuthError(f"No se pudo publicar la respuesta: {res.text}")
        return res.json()
