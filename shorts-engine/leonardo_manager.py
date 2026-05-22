import logging
import os
import uuid
import json
from typing import Any

import requests

from database import JobDatabase

logger = logging.getLogger("leonardo_manager")


class LeonardoManager:
    def __init__(self, db: JobDatabase):
        self.db = db
        self.base_url = "https://cloud.leonardo.ai/api/rest/v1"

    def get_api_key(self) -> str:
        return (self.db.get_setting("LEONARDO_API_KEY") or "").strip()

    def is_configured(self) -> bool:
        return bool(self.get_api_key())

    def _headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
            "content-type": "application/json",
        }

    @staticmethod
    def _safe_json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return "{}"

    @staticmethod
    def _parse_json_string(value: str | None) -> dict:
        if not value:
            return {}
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def create_image_generation(
        self,
        prompt: str,
        *,
        model_id: str | None = None,
        style_ids: list[str] | None = None,
        width: int = 1024,
        height: int = 1024,
        num_images: int = 1,
        negative_prompt: str | None = None,
        seed: int | None = None,
        public: bool = False,
        alchemy: bool = True,
        enhance_prompt: bool = True,
        prompt_magic: bool | None = None,
        init_generation_image_id: str | None = None,
        init_image_id: str | None = None,
        init_strength: float | None = None,
        transparency: str | None = None,
        channel_id: int | None = None,
        job_id: str | None = None,
    ) -> tuple[str, dict]:
        api_key = self.get_api_key()
        if not api_key:
            raise RuntimeError("LEONARDO_API_KEY no está configurada.")

        payload: dict[str, Any] = {
            "prompt": prompt,
            "width": int(width),
            "height": int(height),
            "num_images": max(1, min(int(num_images or 1), 4)),
            "public": bool(public),
            "alchemy": bool(alchemy),
            "enhancePrompt": bool(enhance_prompt),
        }
        if model_id:
            payload["modelId"] = model_id
        if style_ids:
            payload["style_ids"] = [sid for sid in style_ids if sid]
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = int(seed)
        if prompt_magic is not None:
            payload["promptMagic"] = bool(prompt_magic)
        if init_generation_image_id:
            payload["init_generation_image_id"] = init_generation_image_id
        if init_image_id:
            payload["init_image_id"] = init_image_id
        if init_strength is not None:
            payload["init_strength"] = float(init_strength)
        if transparency:
            payload["transparency"] = transparency

        res = requests.post(
            f"{self.base_url}/generations",
            headers=self._headers(api_key),
            json=payload,
            timeout=60,
        )
        try:
            res.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Leonardo image generation error: {res.status_code} {res.text}") from exc
        data = res.json() or {}
        generation_id = self._extract_generation_id(data)
        if not generation_id:
            raise RuntimeError("Leonardo no devolvió generationId.")
        return generation_id, data

    def get_generation(self, generation_id: str) -> dict:
        api_key = self.get_api_key()
        if not api_key:
            raise RuntimeError("LEONARDO_API_KEY no está configurada.")
        res = requests.get(
            f"{self.base_url}/generations/{generation_id}",
            headers=self._headers(api_key),
            timeout=45,
        )
        res.raise_for_status()
        return res.json() or {}

    def poll_generation_once(self, generation_id: str) -> tuple[str, dict]:
        data = self.get_generation(generation_id)
        generation = data.get("generations_by_pk") or data.get("generation") or {}
        status = str(generation.get("status") or "PENDING").upper()
        return status, data

    @staticmethod
    def extract_generated_image_url(data: dict) -> str | None:
        generation = data.get("generations_by_pk") or data.get("generation") or {}
        images = generation.get("generated_images") or []
        if not isinstance(images, list) or not images:
            return None
        first = images[0] or {}
        if isinstance(first, dict):
            return first.get("url") or first.get("imageUrl") or first.get("image_url")
        if isinstance(first, str) and first.startswith("http"):
            return first
        return None

    @staticmethod
    def extract_generated_image_id(data: dict) -> str | None:
        generation = data.get("generations_by_pk") or data.get("generation") or {}
        images = generation.get("generated_images") or []
        if not isinstance(images, list) or not images:
            return None
        first = images[0] or {}
        if isinstance(first, dict):
            return first.get("id")
        return None

    def upload_init_image(self, image_path: str) -> dict:
        api_key = self.get_api_key()
        if not api_key:
            raise RuntimeError("LEONARDO_API_KEY no está configurada.")
        extension = os.path.splitext(image_path)[1].lower().lstrip(".")
        if extension == "jpeg":
            extension = "jpeg"
        if extension not in {"png", "jpg", "jpeg", "webp"}:
            raise RuntimeError("Formato de imagen no soportado para Leonardo.")

        res = requests.post(
            f"{self.base_url}/init-image",
            headers=self._headers(api_key),
            json={"extension": extension},
            timeout=45,
        )
        res.raise_for_status()
        data = res.json() or {}
        upload = data.get("uploadInitImage") or {}
        if not upload.get("id") or not upload.get("url") or not upload.get("fields"):
            raise RuntimeError("Leonardo no devolvió datos de subida presignada.")

        fields = self._parse_json_string(upload.get("fields"))
        with open(image_path, "rb") as fh:
            upload_res = requests.post(
                upload["url"],
                data=fields,
                files={"file": (os.path.basename(image_path), fh, f"image/{extension}")},
                timeout=90,
            )
        if upload_res.status_code not in (200, 204):
            raise RuntimeError(f"Error subiendo imagen a Leonardo: {upload_res.text}")

        return {
            "id": upload["id"],
            "key": upload.get("key"),
            "url": upload.get("url"),
            "fields": fields,
        }

    def create_video_generation(
        self,
        prompt: str,
        *,
        image_id: str,
        image_type: str = "UPLOADED",
        model: str = "MOTION2",
        resolution: str = "RESOLUTION_720",
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
        frame_interpolation: bool | None = None,
        public: bool = False,
        seed: int | None = None,
        negative_prompt: str | None = None,
        prompt_enhance: bool | None = None,
        prompt_enhance_instruction: str | None = None,
        style_ids: list[str] | None = None,
        end_frame_image: dict | None = None,
    ) -> tuple[str, dict]:
        api_key = self.get_api_key()
        if not api_key:
            raise RuntimeError("LEONARDO_API_KEY no está configurada.")
        if not image_id:
            raise RuntimeError("imageId es obligatorio para generar el vídeo.")

        payload: dict[str, Any] = {
            "prompt": prompt,
            "imageId": image_id,
            "imageType": image_type,
            "model": model,
            "resolution": resolution,
            "isPublic": bool(public),
        }
        if width is not None:
            payload["width"] = int(width)
        if height is not None:
            payload["height"] = int(height)
        if duration is not None:
            payload["duration"] = int(duration)
        if frame_interpolation is not None:
            payload["frameInterpolation"] = bool(frame_interpolation)
        if seed is not None:
            payload["seed"] = int(seed)
        if negative_prompt:
            payload["negativePrompt"] = negative_prompt
        if prompt_enhance is not None:
            payload["promptEnhance"] = bool(prompt_enhance)
        if prompt_enhance_instruction:
            payload["promptEnhanceInstruction"] = prompt_enhance_instruction
        if style_ids:
            payload["styleIds"] = [sid for sid in style_ids if sid]
        if end_frame_image:
            payload["endFrameImage"] = end_frame_image

        res = requests.post(
            f"{self.base_url}/generations-image-to-video",
            headers=self._headers(api_key),
            json=payload,
            timeout=60,
        )
        try:
            res.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Leonardo video generation error: {res.status_code} {res.text}") from exc
        data = res.json() or {}
        generation_id = self._extract_generation_id(data)
        if not generation_id:
            raise RuntimeError("Leonardo no devolvió generationId para el vídeo.")
        return generation_id, data

    @staticmethod
    def _find_video_url(value: Any) -> str | None:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("http") and any(token in text.lower() for token in (".mp4", ".mov", ".webm", ".m4v", "video")):
                return text
            return None
        if isinstance(value, list):
            for item in value:
                found = LeonardoManager._find_video_url(item)
                if found:
                    return found
            return None
        if isinstance(value, dict):
            for key in ("url", "videoUrl", "video_url", "downloadUrl", "download_url", "generated_video_url", "generatedVideoUrl"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate
            for nested in value.values():
                found = LeonardoManager._find_video_url(nested)
                if found:
                    return found
        return None

    @staticmethod
    def extract_generated_video_url(data: dict) -> str | None:
        generation = data.get("generations_by_pk") or data.get("generation") or {}
        for key in ("generated_videos", "generatedVideo", "generated_video", "videos", "video"):
            found = LeonardoManager._find_video_url(generation.get(key))
            if found:
                return found
        return LeonardoManager._find_video_url(generation)

    def download_generated_video(self, video_url: str, prompt: str, niche: str, model: str, channel_id: int | None = None) -> dict:
        res = requests.get(video_url, stream=True, timeout=180)
        res.raise_for_status()
        content_type = (res.headers.get("content-type") or "video/mp4").lower()
        ext = "mp4"
        if "webm" in content_type:
            ext = "webm"
        elif "mov" in content_type:
            ext = "mov"

        filename = f"leo_vid_{uuid.uuid4().hex[:10]}.{ext}"
        storage_path = os.path.join("storage", "uploads")
        os.makedirs(storage_path, exist_ok=True)
        file_path = os.path.join(storage_path, filename)
        with open(file_path, "wb") as fh:
            for chunk in res.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)

        media_id = self.db.add_media(
            filename=filename,
            original_name=f"Leonardo video: {prompt[:30]}",
            file_type=f"video/{ext}",
            file_path=file_path,
            size_bytes=os.path.getsize(file_path),
            channel_id=channel_id,
        )
        self.db.tag_as_asset(
            media_id=media_id,
            prompt=prompt,
            niche=niche,
            model=model,
            asset_tag=f"leo_vid_{uuid.uuid4().hex[:6]}",
            is_ai=1,
        )
        logger.info("Vídeo de Leonardo guardado: %s (media_id: %s)", filename, media_id)
        return {
            "media_id": media_id,
            "filename": filename,
            "url": f"/static/uploads/{filename}",
        }

    def download_generated_image(self, image_url: str, prompt: str, niche: str, model: str, channel_id: int | None = None) -> dict:
        res = requests.get(image_url, timeout=90)
        res.raise_for_status()
        content_type = (res.headers.get("content-type") or "image/png").lower()
        ext = "jpg" if "jpeg" in content_type or "jpg" in content_type else "png"

        filename = f"leo_gen_{uuid.uuid4().hex[:10]}.{ext}"
        storage_path = os.path.join("storage", "uploads")
        os.makedirs(storage_path, exist_ok=True)
        file_path = os.path.join(storage_path, filename)
        with open(file_path, "wb") as fh:
            fh.write(res.content)

        media_id = self.db.add_media(
            filename=filename,
            original_name=f"Leonardo: {prompt[:30]}",
            file_type=f"image/{ext}",
            file_path=file_path,
            size_bytes=len(res.content),
            channel_id=channel_id,
        )
        self.db.tag_as_asset(
            media_id=media_id,
            prompt=prompt,
            niche=niche,
            model=model,
            asset_tag=f"leo_{uuid.uuid4().hex[:6]}",
            is_ai=1,
        )
        logger.info("Imagen de Leonardo guardada: %s (media_id: %s)", filename, media_id)
        return {
            "media_id": media_id,
            "filename": filename,
            "url": f"/static/uploads/{filename}",
        }
