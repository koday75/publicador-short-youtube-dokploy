import requests
import logging

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
                elif p_lower == "openai":
                    return self._call_openai(full_prompt, api_key)
                elif p_lower in ["deepseek", "openrouter"]:
                    return self._call_openai_compatible(p_lower, full_prompt, api_key)
                else:
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
            "model": "llama3-70b-8192",
            "messages": messages,
            "response_format": {"type": "json_object"}
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
            "response_format": {"type": "json_object"}
        }
        res = requests.post(url, json=data, headers=headers)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    def _call_openai_compatible(self, provider, prompt, api_key, system_prompt=None):
        urls = {
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions"
        }
        models = {
            "deepseek": "deepseek-chat",
            "openrouter": "meta-llama/llama-3.1-405b"
        }
        
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": models[provider],
            "messages": messages
        }
        res = requests.post(urls[provider], json=data, headers=headers)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    def generate_storyboard(self, content, provider=None):
        """
        Usa la IA para convertir un texto bruto en un storyboard estructurado (JSON).
        Simula lo que antes hacía el nodo 'AI Agent' en n8n. Integra un sistema de fallback iterando sobre los proveedores.
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

        # Prompt de sistema heredado de n8n v3
        sys_msg = (
            "Eres un director de YouTube Shorts virales. Convierte la historia a español. "
            "NO devuelvas texto normal. Responde EXCLUSIVAMENTE con un JSON valido sin texto adicional. "
            "Divide la historia en entre 4 y 6 escenas cortas (1-2 frases cada una). "
            "Estructura requerida exacta: {\"scenes\": [{\"text\": \"Texto de la escena hablado\", "
            "\"image_prompt\": \"Descripción visual detallada para generar la imagen de esta escena en inglés, muy visual, "
            "estilo fotorealista o cinematográfico, formato 9:16\", \"subtitle_pos\": 8, \"subtitle_size\": 48}]}"
        )

        user_content = f"Historia original para procesar:\n{content}"

        import json
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
                
                # Limpieza básica de la respuesta IA (quitar bloques de código markdown)
                clean_text = res_text.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_text)
            
            except Exception as e:
                logger.error(f"Fallo en {current_provider}: {e}")
                last_error = e
                continue
                
        logger.error(f"Todos los proveedores fallaron. Último error: {last_error}")
        raise Exception(f"Generación IA fallida tras intentar con múltiples proveedores. Detalles: {last_error}")
