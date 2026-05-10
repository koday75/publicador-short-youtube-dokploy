import cloudinary
import cloudinary.uploader
import cloudinary.api
from cloudinary.utils import cloudinary_url
import logging

logger = logging.getLogger(__name__)

class CloudinaryManager:
    def __init__(self, db):
        self.db = db
        self.current_idx = 0
        self.accounts = []
        self._load_accounts()

    def _load_accounts(self):
        self.accounts = []
        for i in range(1, 6):
            name = self.db.get_setting(f'CLOUDINARY_{i}_NAME')
            key = self.db.get_setting(f'CLOUDINARY_{i}_KEY')
            secret = self.db.get_setting(f'CLOUDINARY_{i}_SECRET')
            if name and key and secret:
                self.accounts.append({
                    "cloud_name": name,
                    "api_key": key,
                    "api_secret": secret
                })
        logger.info(f"CloudinaryManager: {len(self.accounts)} cuentas configuradas.")

    def get_next_config(self):
        if not self.accounts:
            return None
        config = self.accounts[self.current_idx]
        self.current_idx = (self.current_idx + 1) % len(self.accounts)
        return config

    def generate_background(self, base_image_path, prompt, output_path):
        """
        Sube una imagen base y le aplica el Generative Background Replace usando el prompt.
        """
        config = self.get_next_config()
        if not config:
            raise Exception("No hay cuentas de Cloudinary configuradas.")

        cloudinary.config(
            cloud_name=config["cloud_name"],
            api_key=config["api_key"],
            api_secret=config["api_secret"]
        )

        try:
            # Subir imagen base
            upload_result = cloudinary.uploader.upload(base_image_path)
            public_id = upload_result['public_id']

            # Aplicar transformación Generativa
            # 'gen_background_replace:prompt_...' es la sintaxis de Cloudinary
            transformation = [
                {'effect': f"gen_background_replace:prompt_{prompt}"},
                {'width': 1080, 'height': 1920, 'crop': 'fill', 'gravity': 'auto'}
            ]
            
            # Obtener URL
            url, _ = cloudinary_url(public_id, transformation=transformation)
            
            # Descargar el resultado al output_path
            import requests
            r = requests.get(url)
            if r.status_code == 200:
                with open(output_path, 'wb') as f:
                    f.write(r.content)
                logger.info(f"Imagen generada con Cloudinary guardada en {output_path}")
                return output_path
            else:
                raise Exception(f"Error al descargar de Cloudinary: {r.status_code}")

        except Exception as e:
            logger.error(f"Cloudinary Error: {str(e)}")
            raise e
