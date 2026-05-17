import json
import logging

import requests

logger = logging.getLogger(__name__)


class AIManager:
    def __init__(self, db):
        self.db = db

    def _get_api_key(self, provider):
        val = self.db.get_setting(f"{provider.upper()}_API_KEY")
        return val.strip() if val else None

    def optimize_text(self, text, provider, template_prompt):
        configured_providers = []
        if provider and self._get_api_key(provider):
            configured_providers.append(provider)
        else:
            for p in ["GROQ", "DEEPSEEK", "OPENROUTER", "OPENAI"]:
                if self._get_api_key(p):
                    configured_providers.append(p)

        if not configured_providers:
            raise Exception("No se ha configurado ninguna API de IA.")

        full_prompt = f"{template_prompt}\n\nTexto original:\n{text}"
        last_error = None

        for current_provider in configured_providers:
            api_key = self._get_api_key(current_provider)
            try:
                p_lower = current_provider.lower()
                if p_lower == "groq":
                    return self._call_groq(full_prompt, api_key)
                if p_lower == "openai":
                    return self._call_openai(full_prompt, api_key)
                if p_lower in ["deepseek", "openrouter"]:
                    return self._call_openai_compatible(p_lower, full_prompt, api_key)
                raise Exception(f"Proveedor {current_provider} no soportado.")
            except Exception as e:
                logger.error(f"Fallo llamando a {current_provider}: {e}")
                last_error = e
                continue

        raise Exception(f"Todos los proveedores de IA fallaron. Detalles: {last_error}")

    def _call_groq(self, prompt, api_key, system_prompt=None):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        res = requests.post(url, json=data, headers=headers)
        if not res.ok:
            logger.error(f"Groq API Error Output: {res.text}")
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    def _call_openai(self, prompt, api_key, system_prompt=None):
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": "gpt-4o",
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        res = requests.post(url, json=data, headers=headers)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    def _call_openai_compatible(self, provider, prompt, api_key, system_prompt=None):
        urls = {
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        }
        models = {
            "deepseek": "deepseek-chat",
            "openrouter": "meta-llama/llama-3.1-405b",
        }

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": models[provider],
            "messages": messages,
        }
        res = requests.post(urls[provider], json=data, headers=headers)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    def generate_storyboard(self, content, provider=None):
        """
        Convierte un texto bruto en un storyboard estructurado (JSON).
        """
        configured_providers = []
        if provider and self._get_api_key(provider):
            configured_providers.append(provider)
        else:
            for p in ["GROQ", "DEEPSEEK", "OPENROUTER", "OPENAI"]:
                if self._get_api_key(p):
                    configured_providers.append(p)

        if not configured_providers:
            raise Exception("No se ha configurado ninguna API de IA (Groq, DeepSeek, OpenRouter, OpenAI) en Ajustes.")

        sys_msg = (
            "Eres un director de YouTube Shorts virales. Convierte la historia a español. "
            "No devuelvas texto normal. Responde exclusivamente con un JSON válido sin texto adicional. "
            "Divide la historia en entre 4 y 6 escenas cortas (1-2 frases cada una). "
            "Estructura requerida exacta: {\"scenes\": [{\"text\": \"Texto de la escena hablado\", "
            "\"image_prompt\": \"Descripción visual detallada para generar la imagen de esta escena en inglés, muy visual, "
            "estilo fotorrealista o cinematográfico, formato 9:16\", \"subtitle_pos\": 8, \"subtitle_size\": 48}]}"
        )

        user_content = f"Historia original para procesar:\n{content}"
        last_error = None

        for current_provider in configured_providers:
            api_key = self._get_api_key(current_provider)
            logger.info(f"Intentando generar storyboard con {current_provider}...")
            try:
                p_lower = current_provider.lower()
                if p_lower == "groq":
                    res_text = self._call_groq(user_content, api_key, system_prompt=sys_msg)
                elif p_lower == "openai":
                    res_text = self._call_openai(user_content, api_key, system_prompt=sys_msg)
                else:
                    res_text = self._call_openai_compatible(p_lower, user_content, api_key, system_prompt=sys_msg)

                clean_text = res_text.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_text)
            except Exception as e:
                logger.error(f"Fallo en {current_provider}: {e}")
                last_error = e
                continue

        logger.error(f"Todos los proveedores fallaron. Último error: {last_error}")
        raise Exception(f"Generación IA fallida tras intentar con múltiples proveedores. Detalles: {last_error}")

    def generate_comment_reply(self, comment_text, provider=None, video_title=None, channel_name=None):
        """
        Genera un borrador de respuesta para un comentario de YouTube.
        """
        configured_providers = []
        if provider and self._get_api_key(provider):
            configured_providers.append(provider)
        else:
            for p in ["GROQ", "DEEPSEEK", "OPENROUTER", "OPENAI"]:
                if self._get_api_key(p):
                    configured_providers.append(p)

        if not configured_providers:
            raise Exception("No se ha configurado ninguna API de IA.")

        sys_msg = (
            "Eres el community manager de un canal de YouTube. "
            "Redacta una respuesta breve, natural y educada en español castellano. "
            "No menciones que eres una IA. "
            "Devuelve solo JSON válido con esta estructura exacta: {\"reply\":\"texto\"}."
        )
        user_content = "\n".join([
            f"Canal: {channel_name or 'Canal de YouTube'}",
            f"Vídeo: {video_title or 'Vídeo del canal'}",
            f"Comentario: {comment_text}",
            "Escribe una respuesta pública útil y concisa para ese comentario.",
        ])

        last_error = None

        for current_provider in configured_providers:
            api_key = self._get_api_key(current_provider)
            logger.info(f"Intentando generar respuesta de comentario con {current_provider}...")
            try:
                p_lower = current_provider.lower()
                if p_lower == "groq":
                    res_text = self._call_groq(user_content, api_key, system_prompt=sys_msg)
                elif p_lower == "openai":
                    res_text = self._call_openai(user_content, api_key, system_prompt=sys_msg)
                else:
                    res_text = self._call_openai_compatible(p_lower, user_content, api_key, system_prompt=sys_msg)

                clean_text = res_text.replace("```json", "").replace("```", "").strip()
                try:
                    data = json.loads(clean_text)
                    reply = str((data or {}).get("reply") or "").strip()
                    if reply:
                        return reply
                except Exception:
                    pass

                return clean_text.strip()
            except Exception as e:
                logger.error(f"Fallo en {current_provider} al generar respuesta de comentario: {e}")
                last_error = e
                continue

        logger.error(f"Todos los proveedores fallaron al generar respuesta de comentario. Último error: {last_error}")
        raise Exception(f"Generación IA de respuesta fallida. Detalles: {last_error}")

    def summarize_script_source(self, text, provider=None, title=None, source_language=None):
        """
        Resume una transcripción o texto fuente siempre en español castellano.
        """
        configured_providers = []
        if provider and self._get_api_key(provider):
            configured_providers.append(provider)
        else:
            for p in ["GROQ", "DEEPSEEK", "OPENROUTER", "OPENAI"]:
                if self._get_api_key(p):
                    configured_providers.append(p)

        if not configured_providers:
            raise Exception("No se ha configurado ninguna API de IA.")

        sys_msg = (
            "Eres un analista editorial para canales de YouTube. "
            "Lee la transcripción o texto fuente y devuelve siempre un resumen claro en español castellano. "
            "Si el texto original está en otro idioma, no hace falta traducirlo completo, "
            "pero el resumen final debe estar en español correcto. "
            "Responde solo con JSON válido usando esta estructura exacta: "
            "{\"summary\":\"resumen en español\", \"translated_text\":\"\"}."
        )
        clipped_text = (text or "").strip()[:18000]
        user_content = "\n".join([
            f"Título del vídeo: {title or 'Sin título'}",
            f"Idioma detectado: {source_language or 'desconocido'}",
            "Haz un resumen en español con lo más importante, útil para preparar un guion corto.",
            "",
            clipped_text,
        ])

        last_error = None
        for current_provider in configured_providers:
            api_key = self._get_api_key(current_provider)
            logger.info(f"Intentando resumir fuente de guion con {current_provider}...")
            try:
                p_lower = current_provider.lower()
                if p_lower == "groq":
                    res_text = self._call_groq(user_content, api_key, system_prompt=sys_msg)
                elif p_lower == "openai":
                    res_text = self._call_openai(user_content, api_key, system_prompt=sys_msg)
                else:
                    res_text = self._call_openai_compatible(p_lower, user_content, api_key, system_prompt=sys_msg)

                clean_text = res_text.replace("```json", "").replace("```", "").strip()
                try:
                    data = json.loads(clean_text)
                except Exception:
                    data = {"summary": clean_text.strip(), "translated_text": ""}

                summary = str((data or {}).get("summary") or "").strip()
                translated_text = str((data or {}).get("translated_text") or "").strip()
                if summary:
                    return {"summary": summary, "translated_text": translated_text}
            except Exception as e:
                logger.error(f"Fallo en {current_provider} al resumir fuente de guion: {e}")
                last_error = e
                continue

        raise Exception(f"No se pudo resumir la fuente con la IA. Detalles: {last_error}")

    def generate_script_summary(self, sources, topic_title=None, topic_description=None, provider=None, max_scenes=6):
        """
        Genera un guion breve en castellano a partir de varias transcripciones.
        """
        configured_providers = []
        if provider and self._get_api_key(provider):
            configured_providers.append(provider)
        else:
            for p in ["GROQ", "DEEPSEEK", "OPENROUTER", "OPENAI"]:
                if self._get_api_key(p):
                    configured_providers.append(p)

        if not configured_providers:
            raise Exception("No se ha configurado ninguna API de IA.")

        max_scenes = max(1, min(int(max_scenes or 6), 6))
        sys_msg = (
            "Eres un guionista experto en videos cortos. "
            "Usa las transcripciones aportadas para crear un guion original en espanol castellano. "
            "No copies frases largas literalmente. Condensa, compara y une la informacion importante. "
            f"El guion debe tener como maximo {max_scenes} escenas. "
            "Responde solo con JSON valido usando esta estructura exacta: "
            "{\"summary\":\"resumen general en espanol\", \"script\":\"guion por escenas\", "
            "\"scenes\":[{\"title\":\"escena\", \"text\":\"texto narrado\"}]}."
        )

        source_blocks = []
        for index, source in enumerate(sources or [], start=1):
            title = str(source.get("title") or source.get("source_url") or f"Fuente {index}").strip()
            text = str(source.get("raw_text") or source.get("translated_text") or "").strip()
            if not text:
                continue
            source_blocks.append(f"Fuente {index}: {title}\n{text[:9000]}")

        if not source_blocks:
            raise Exception("No hay transcripciones guardadas para crear el resumen.")

        user_content = "\n\n".join([
            f"Tema: {topic_title or 'Tema sin titulo'}",
            f"Enfoque: {topic_description or ''}",
            "Transcripciones:",
            "\n\n---\n\n".join(source_blocks)[:24000],
        ])

        last_error = None
        for current_provider in configured_providers:
            api_key = self._get_api_key(current_provider)
            logger.info(f"Intentando generar resumen de guion con {current_provider}...")
            try:
                p_lower = current_provider.lower()
                if p_lower == "groq":
                    res_text = self._call_groq(user_content, api_key, system_prompt=sys_msg)
                elif p_lower == "openai":
                    res_text = self._call_openai(user_content, api_key, system_prompt=sys_msg)
                else:
                    res_text = self._call_openai_compatible(p_lower, user_content, api_key, system_prompt=sys_msg)

                clean_text = res_text.replace("```json", "").replace("```", "").strip()
                try:
                    data = json.loads(clean_text)
                except Exception:
                    data = {"summary": clean_text, "script": clean_text, "scenes": []}

                scenes = data.get("scenes") if isinstance(data, dict) else []
                if isinstance(scenes, list):
                    scenes = scenes[:max_scenes]
                else:
                    scenes = []
                summary = str((data or {}).get("summary") or "").strip()
                script = str((data or {}).get("script") or "").strip()
                if not script and scenes:
                    script = "\n\n".join(
                        f"Escena {idx}: {str(scene.get('text') or '').strip()}"
                        for idx, scene in enumerate(scenes, start=1)
                        if isinstance(scene, dict)
                    )
                if summary or script:
                    return {"summary": summary, "script": script or summary, "scenes": scenes}
            except Exception as e:
                logger.error(f"Fallo en {current_provider} al generar resumen de guion: {e}")
                last_error = e
                continue

        raise Exception(f"No se pudo generar el resumen del tema. Detalles: {last_error}")
