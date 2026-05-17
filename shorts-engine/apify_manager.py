import logging
from typing import Any

import requests

from database import JobDatabase

logger = logging.getLogger("apify_manager")


class ApifyManager:
    def __init__(self, db: JobDatabase):
        self.db = db
        self.api_base = "https://api.apify.com/v2"
        self.account_count = 4
        # Actor específico para transcript/subtítulos.
        self.default_youtube_actor = "marklp/youtube-transcript"

    def _get_api_key_by_index(self, index: int) -> str:
        if index < 1 or index > self.account_count:
            return ""
        return (self.db.get_setting(f"APIFY_API_KEY_{index}") or "").strip()

    def _get_active_index(self) -> int:
        try:
            return max(1, min(self.account_count, int(self.db.get_setting("APIFY_CURRENT_KEY_INDEX", "1") or "1")))
        except Exception:
            return 1

    def _set_active_index(self, index: int):
        index = max(1, min(self.account_count, int(index)))
        self.db.set_setting("APIFY_CURRENT_KEY_INDEX", str(index))

    def _actor_path(self, actor_id: str | None) -> str:
        actor = (actor_id or self.default_youtube_actor).strip()
        return actor.replace("/", "~")

    def get_account_status(self, index: int) -> dict[str, Any]:
        token = self._get_api_key_by_index(index)
        result = {
            "key_index": index,
            "key_name": f"APIFY_API_KEY_{index}",
            "configured": bool(token),
            "active": index == self._get_active_index(),
            "status": "not_configured",
            "account_name": None,
            "message": None,
        }
        if not token:
            return result

        try:
            res = requests.get(
                f"{self.api_base}/users/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            data = res.json() if res.content else {}
            if res.ok and data.get("data"):
                me = data["data"]
                result.update({
                    "status": "ok",
                    "account_name": me.get("username") or me.get("email") or me.get("id"),
                    "message": "Cuenta válida",
                })
            else:
                result.update({
                    "status": "error",
                    "message": self._extract_error_message(data) or "No se pudo validar la cuenta.",
                })
        except Exception as exc:
            logger.warning("No se pudo validar Apify API key %s: %s", index, exc)
            result.update({
                "status": "error",
                "message": str(exc),
            })
        return result

    def get_accounts_status(self) -> list[dict[str, Any]]:
        return [self.get_account_status(i) for i in range(1, self.account_count + 1)]

    def get_valid_api_key(self, skip_indices: set[int] | None = None) -> str | None:
        skip_indices = skip_indices or set()
        start_index = self._get_active_index()
        for offset in range(self.account_count):
            current_index = ((start_index - 1 + offset) % self.account_count) + 1
            if current_index in skip_indices:
                continue
            api_key = self._get_api_key_by_index(current_index)
            if not api_key:
                continue
            try:
                res = requests.get(
                    f"{self.api_base}/users/me",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=20,
                )
                if res.ok:
                    self._set_active_index(current_index)
                    return api_key
            except Exception as exc:
                logger.warning("Apify key %s no válida: %s", current_index, exc)
                continue
        return None

    def run_youtube_scraper(self, input_data: dict[str, Any], actor_id: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        api_key = self.get_valid_api_key()
        if not api_key:
            raise RuntimeError("No hay cuentas de Apify configuradas o válidas.")
        actor_path = self._actor_path(actor_id)
        url = f"{self.api_base}/acts/{actor_path}/run-sync-get-dataset-items"
        res = requests.post(
            url,
            params={"token": api_key, "clean": "true"},
            json=input_data,
            timeout=180,
        )
        if not res.ok:
            detail = self._extract_error_message(self._safe_json(res))
            raise RuntimeError(detail or f"Apify respondió con código {res.status_code}.")
        return self._safe_json(res)

    def fetch_youtube_transcript(self, source_url: str, actor_id: str | None = None) -> dict[str, Any]:
        payload = {
            "videoUrl": source_url,
            # Preferimos español si existe; si no, el actor cae a otro idioma disponible.
            "languageCode": "es",
            "includeTxt": True,
            "includeJson": True,
            "includeMd": False,
            "includeSrt": False,
            "includeCsv": False,
        }
        items = self.run_youtube_scraper(payload, actor_id=actor_id)

        summary_row: dict[str, Any] = {}
        if isinstance(items, list) and items:
            summary_row = items[0]
        elif isinstance(items, dict):
            data = items.get("items") or items.get("data") or []
            if isinstance(data, list) and data:
                summary_row = data[0]
            else:
                summary_row = items

        transcript_text = self._extract_transcript_text(summary_row)
        if transcript_text:
            summary_row["transcript"] = transcript_text
            summary_row["text"] = transcript_text

        if not summary_row.get("thumbnailUrl"):
            video_id = summary_row.get("videoId")
            if video_id:
                summary_row["thumbnailUrl"] = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        return summary_row

    def _extract_transcript_text(self, item: dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""

        for key in ("transcript", "text", "content", "caption"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        transcript_txt_url = item.get("transcript_txt")
        if transcript_txt_url:
            text = self._download_text(transcript_txt_url)
            if text:
                return text

        transcript_json_url = item.get("transcript_json")
        if transcript_json_url:
            text = self._download_transcript_json(transcript_json_url)
            if text:
                return text

        return ""

    def _download_text(self, url: str) -> str:
        api_key = self.get_valid_api_key()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            res = requests.get(url, headers=headers, timeout=60)
            if res.ok and res.text:
                return res.text.strip()
        except Exception as exc:
            logger.warning("No se pudo descargar transcript_txt desde Apify: %s", exc)
        return ""

    def _download_transcript_json(self, url: str) -> str:
        api_key = self.get_valid_api_key()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            res = requests.get(url, headers=headers, timeout=60)
            payload = res.json() if res.ok else {}
        except Exception as exc:
            logger.warning("No se pudo descargar transcript_json desde Apify: %s", exc)
            return ""

        return self._flatten_transcript_segments(payload)

    def _flatten_transcript_segments(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    cleaned = item.strip()
                elif isinstance(item, dict):
                    cleaned = str(
                        item.get("text")
                        or item.get("subtitle")
                        or item.get("caption")
                        or item.get("value")
                        or ""
                    ).strip()
                else:
                    cleaned = ""
                if cleaned:
                    parts.append(cleaned)
            return " ".join(parts).strip()
        if isinstance(value, dict):
            nested = (
                value.get("segments")
                or value.get("items")
                or value.get("captions")
                or value.get("transcript")
                or value.get("subtitles")
            )
            if nested:
                return self._flatten_transcript_segments(nested)
        return ""

    @staticmethod
    def _safe_json(response: requests.Response | None) -> dict[str, Any] | list[dict[str, Any]]:
        if response is None:
            return {}
        try:
            return response.json()
        except Exception:
            return {"raw": response.text[:1000] if response.text else ""}

    @staticmethod
    def _extract_error_message(payload: dict[str, Any] | list[dict[str, Any]] | None) -> str | None:
        if not payload or not isinstance(payload, dict):
            return None
        for key in ("message", "msg", "error", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("message", "msg", "error", "detail"):
                value = data.get(key)
                if value:
                    return str(value)
        return None
