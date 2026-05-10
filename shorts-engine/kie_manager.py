import requests
import time
import os
import uuid
import json
import logging
from database import JobDatabase

logger = logging.getLogger("kie_manager")

class KieAiManager:
    def __init__(self, db: JobDatabase):
        self.db = db
        self.base_url = "https://api.kie.ai/api/v1/jobs"
        self.credit_url = "https://api.kie.ai/api/v1/chat/credit"

    def _get_api_key_by_index(self, index: int) -> str:
        """Returns a specific API key by its index (1-5)."""
        return self.db.get_setting(f"KIE_API_KEY_{index}") or ""

    def get_valid_api_key(self, skip_indices: set = None) -> str | None:
        """Finds the first configured API key that has >= 5 credits."""
        if skip_indices is None:
            skip_indices = set()
            
        index_str = self.db.get_setting("KIE_CURRENT_KEY_INDEX") or "1"
        try:
            start_index = int(index_str)
        except ValueError:
            start_index = 1
            
        for offset in range(5):
            current_index = ((start_index - 1 + offset) % 5) + 1
            
            if current_index in skip_indices:
                continue
                
            api_key = self.db.get_setting(f"KIE_API_KEY_{current_index}")
            
            if not api_key:
                continue
                
            # Verificar créditos
            credit_info = self.get_credits(api_key)
            # Aumentamos umbral a 5 créditos (margen de seguridad para imágenes)
            if credit_info.get("status") == "ok" and credit_info.get("credits", 0) >= 5:
                self.db.set_setting("KIE_CURRENT_KEY_INDEX", str(current_index))
                logger.info(f"Kie.ai: Seleccionada llave {current_index} con {credit_info['credits']} créditos.")
                return api_key
            else:
                logger.warning(f"Kie.ai: Llave {current_index} ignorada (Créditos bajos o error: {credit_info.get('credits')} cr).")
                
        return None

    def get_credits(self, api_key: str) -> dict:
        """Gets the credit balance for a given API key via the Kie.ai credit endpoint."""
        if not api_key:
            return {"status": "error", "credits": 0, "msg": "Clave no configurada"}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        try:
            res = requests.get(self.credit_url, headers=headers, timeout=15)
            res.raise_for_status()
            data = res.json()
            if data.get("code") in [0, 200, "0", "200"]:
                return {"status": "ok", "credits": data.get("data", 0)}
            else:
                return {"status": "error", "credits": 0, "msg": data.get("msg", "Error desconocido")}
        except Exception as e:
            return {"status": "error", "credits": 0, "msg": str(e)}

    def _extract_image_url(self, data: dict) -> str | None:
        """
        Robustly extracts the image URL from any Kie.ai response structure.
        Handles: list of dicts, list of strings, raw dicts, and resultJson strings.
        """
        logger.info(f"Kie.ai result data: {json.dumps(data, ensure_ascii=False)[:600]}")

        # 1. Try 'results' field
        results = data.get("results") or []
        if isinstance(results, str):
            try:
                results = json.loads(results)
            except Exception:
                results = []

        if isinstance(results, list) and len(results) > 0:
            first = results[0]
            if isinstance(first, dict):
                url = first.get("url") or first.get("imageUrl") or first.get("image_url")
                if url:
                    return url
            elif isinstance(first, str) and first.startswith("http"):
                return first

        if isinstance(results, dict):
            url = results.get("url") or results.get("imageUrl")
            if url:
                return url

        # 2. Try 'resultJson' field
        result_json_str = data.get("resultJson")
        if result_json_str:
            try:
                result_json = json.loads(result_json_str)
                
                # Formato nuevo: Diccionario con array 'resultUrls'
                if isinstance(result_json, dict):
                    urls = result_json.get("resultUrls") or result_json.get("images") or []
                    if isinstance(urls, list) and len(urls) > 0:
                        if isinstance(urls[0], str) and urls[0].startswith("http"):
                            return urls[0]
                    # Alternativas en diccionario
                    for key in ["url", "imageUrl", "image_url"]:
                        if result_json.get(key):
                            return result_json[key]
                            
                # Formato antiguo: Array directo
                elif isinstance(result_json, list) and len(result_json) > 0:
                    first = result_json[0]
                    if isinstance(first, dict):
                        url = first.get("url") or first.get("imageUrl")
                        if url:
                            return url
                    elif isinstance(first, str) and first.startswith("http"):
                        return first
            except Exception:
                pass

        # 3. Scan top-level data keys for any URL
        for key in ["url", "imageUrl", "image_url", "outputUrl", "resultUrl"]:
            if key in data and data[key]:
                return data[key]

        logger.warning(f"No se pudo extraer URL de la respuesta de Kie.ai. Data: {str(data)[:300]}")
        return None

    def create_remote_task(self, prompt: str, model: str = None):
        """Creates a task on Kie.ai and returns the task_id. Retries with other keys if credits are insufficient."""
        if not model:
            model = "seedream/5-lite-text-to-image"

        skipped_indices = set()
        
        for attempt in range(5):# Máximo 5 intentos (uno por cuenta)
            api_key = self.get_valid_api_key(skip_indices=skipped_indices)
            if not api_key:
                raise Exception("Todas las cuentas de Kie.ai (1-5) están sin saldo o no configuradas. Por favor, recarga créditos.")

            # Obtener el índice actual para poder "saltarlo" si falla
            current_index_str = self.db.get_setting("KIE_CURRENT_KEY_INDEX") or "1"
            current_index = int(current_index_str)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": model,
                "callBackUrl": "",
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "quality": "basic",
                    "num_images": 1
                }
            }

            logger.info(f"Kie.ai (Intento {attempt+1}, Cuenta {current_index}): Enviando createTask...")
            try:
                res = requests.post(f"{self.base_url}/createTask", json=payload, headers=headers, timeout=30)
                res_data = res.json()

                if not res.ok or res_data.get("code") not in [0, 200, "0", "200"]:
                    msg = str(res_data.get("msg", "Error desconocido")).lower()
                    
                    if "credits insufficient" in msg or "balance isn't enough" in msg:
                        logger.warning(f"Kie.ai: Cuenta {current_index} sin saldo suficiente. Rotando...")
                        skipped_indices.add(current_index)
                        
                        # Forzamos que la siguiente ejecución empiece en la siguiente llave
                        next_idx = (current_index % 5) + 1
                        self.db.set_setting("KIE_CURRENT_KEY_INDEX", str(next_idx))
                        continue # Reintentar bucle con nueva llave
                    
                    raise Exception(f"Error de Kie.ai: {res_data.get('msg')}")

                task_id = res_data.get("data", {}).get("taskId")
                if not task_id:
                    raise Exception("Kie.ai no devolvió un taskId.")

                return task_id, api_key

            except requests.exceptions.RequestException as e:
                logger.error(f"Error de red con Kie.ai (Cuenta {current_index}): {str(e)}")
                skipped_indices.add(current_index)
                continue

        raise Exception("Se agotaron los reintentos. Ninguna de las 5 cuentas de Kie.ai ha podido procesar la petición.")

    def poll_task_once(self, task_id: str, api_key: str):
        """Polls the status of a single task one time."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        status_res = requests.get(
            f"{self.base_url}/recordInfo",
            params={"taskId": task_id},
            headers=headers,
            timeout=20
        )
        status_res.raise_for_status()
        status_data = status_res.json()

        task_data = status_data.get("data", {})
        raw_status = task_data.get("state") or task_data.get("status") or "processing"
        task_status = str(raw_status).lower()

        return task_status, task_data

    def _process_completed_image(self, url: str, prompt: str, niche: str, model: str):
        """Downloads the image from URL and registers it in the database."""
        try:
            res = requests.get(url, timeout=60)
            res.raise_for_status()

            content_type = res.headers.get("content-type", "image/png")
            ext = "jpg" if "jpeg" in content_type else "png"

            filename = f"ia_gen_{uuid.uuid4().hex[:10]}.{ext}"
            storage_path = os.path.join("storage", "uploads")
            os.makedirs(storage_path, exist_ok=True)

            file_path = os.path.join(storage_path, filename)
            with open(file_path, "wb") as f:
                f.write(res.content)

            file_size = len(res.content)
            media_id = self.db.add_media(
                filename=filename,
                original_name=f"IA: {prompt[:30]}",
                file_type=f"image/{ext}",
                file_path=file_path,
                size_bytes=file_size
            )

            self.db.tag_as_asset(
                media_id=media_id,
                prompt=prompt,
                niche=niche,
                model=model,
                asset_tag=f"ia_{uuid.uuid4().hex[:6]}",
                is_ai=1
            )

            logger.info(f"Imagen de IA guardada: {filename} (media_id: {media_id})")
            return {
                "media_id": media_id,
                "filename": filename,
                "url": f"/static/uploads/{filename}"
            }

        except Exception as e:
            logger.error(f"Error procesando imagen completada: {str(e)}")
            raise e
