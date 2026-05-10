import os
import logging
import asyncio
import subprocess

logger = logging.getLogger(__name__)

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

# Edge-TTS voice catalogue (Spanish)
EDGE_TTS_VOICES = {
    "es-ES-AlvaroNeural": {"name": "Álvaro", "gender": "male", "locale": "es-ES"},
    "es-ES-ElviraNeural": {"name": "Elvira", "gender": "female", "locale": "es-ES"},
    "es-MX-JorgeNeural": {"name": "Jorge", "gender": "male", "locale": "es-MX"},
    "es-MX-DaliaNeural": {"name": "Dalia", "gender": "female", "locale": "es-MX"},
    "es-AR-TomasNeural": {"name": "Tomás", "gender": "male", "locale": "es-AR"},
    "es-AR-ElenaNeural": {"name": "Elena", "gender": "female", "locale": "es-AR"},
    "es-CO-GonzaloNeural": {"name": "Gonzalo", "gender": "male", "locale": "es-CO"},
    "es-CO-SalomeNeural": {"name": "Salomé", "gender": "female", "locale": "es-CO"},
}

# gTTS voice presets
GTTS_VOICES = {
    "es": {"name": "Español España", "locale": "es-ES"},
    "com.mx": {"name": "Español México", "locale": "es-MX"},
    "us": {"name": "Español EE.UU.", "locale": "es-US"},
    "co.uk": {"name": "English UK", "locale": "en-GB"},
}


class ElevenLabsManager:
    """Multi-engine TTS Manager: supports gTTS and Edge-TTS"""

    def __init__(self, api_keys: list = None):
        self.api_keys = []
        self.current_key_index = 0

    def get_current_key(self):
        return None

    def text_to_speech(
        self,
        text: str,
        voice_id: str = "es",
        output_path: str = "output.mp3",
        lang: str = "es",
        engine: str = "gtts",
        speed: float = 1.0
    ):
        """
        Generate TTS audio.
        
        Args:
            text: The text to speak.
            voice_id: Depends on engine:
                - gtts: TLD accent code (es, com.mx, us, co.uk)
                - edge-tts: Voice name (e.g. 'es-ES-AlvaroNeural')
            output_path: Path to save the MP3 file.
            lang: Language code (used by gTTS).
            engine: "gtts" or "edge-tts".
            speed: Speed multiplier (0.5-2.0). Only used by Edge-TTS.
        """
        if engine == "edge-tts" and EDGE_TTS_AVAILABLE:
            return self._edge_tts(text, voice_id, output_path, speed)
        else:
            return self._gtts(text, voice_id, output_path, lang)

    def _gtts(self, text: str, voice_id: str, output_path: str, lang: str = "es"):
        """Generate audio using gTTS."""
        if not GTTS_AVAILABLE:
            logger.error("gTTS no instalado.")
            return self._create_silent_audio(output_path)

        tld = "es"
        current_lang = lang

        if voice_id in GTTS_VOICES:
            tld = voice_id
        elif voice_id in ["es", "com.mx", "us"]:
            tld = voice_id
            current_lang = "es"
        elif voice_id == "co.uk":
            tld = "co.uk"
            current_lang = "en"

        try:
            logger.info(f"Generando voz con gTTS (Acento: {tld}, Idioma: {current_lang})")
            tts = gTTS(text=text, lang=current_lang, tld=tld, slow=False)
            tts.save(output_path)
            return output_path
        except Exception as e:
            logger.error(f"Error en gTTS: {e}. Creando audio de silencio.")
            return self._create_silent_audio(output_path)

    def _edge_tts(self, text: str, voice_id: str, output_path: str, speed: float = 1.0):
        """Generate audio using Edge-TTS via CLI subprocess (avoids asyncio event loop conflicts)."""
        if not EDGE_TTS_AVAILABLE:
            logger.warning("edge-tts no instalado. Usando gTTS como fallback.")
            return self._gtts(text, "es", output_path)

        # Default voice if not found
        if voice_id not in EDGE_TTS_VOICES:
            voice_id = "es-ES-AlvaroNeural"

        # Convert speed multiplier to Edge-TTS rate string
        # 1.0 = +0%, 1.5 = +50%, 0.7 = -30%
        rate_percent = int((speed - 1.0) * 100)
        rate_str = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"

        try:
            logger.info(f"Generando voz con Edge-TTS (Voz: {voice_id}, Velocidad: {rate_str})")
            # Use CLI to sidestep FastAPI's running event loop
            result = subprocess.run(
                ['edge-tts', '--voice', voice_id, '--rate', rate_str, '--text', text, '--write-media', output_path],
                check=True, capture_output=True, text=True, timeout=60
            )
            return output_path
        except FileNotFoundError:
            logger.error("edge-tts CLI no encontrado en PATH. Asegúrate de instalar edge-tts. Usando gTTS como fallback.")
            return self._gtts(text, "es", output_path)
        except subprocess.TimeoutExpired:
            logger.error("Edge-TTS timeout. Usando gTTS como fallback.")
            return self._gtts(text, "es", output_path)
        except subprocess.CalledProcessError as e:
            logger.error(f"Error en Edge-TTS CLI: {e.stderr}. Usando gTTS como fallback.")
            return self._gtts(text, "es", output_path)
        except Exception as e:
            logger.error(f"Error inesperado en Edge-TTS: {e}. Usando gTTS como fallback.")
            return self._gtts(text, "es", output_path)

    def _create_silent_audio(self, output_path: str, duration: int = 5):
        """Generate a silent audio file using FFmpeg."""
        subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i',
             f'anullsrc=r=44100:cl=mono', '-t', str(duration), output_path],
            check=True, capture_output=True
        )
        return output_path
