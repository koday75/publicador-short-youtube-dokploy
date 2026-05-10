# 🎥 Publicador Automático de YouTube Shorts - Cinema Studio 🎬

Este proyecto es una solución integral para la creación y publicación automatizada de **YouTube Shorts**. Ha evolucionado a una plataforma de producción completa con **Cinema Storyboard**, **Galería Multimedia** y **Hub de IA Multicuenta**.

## 🚀 Características Principales

-   **Cinema Storyboard**: Editor profesional basado en escenas con transiciones y sincronización de voz.
-   **IA Generativa (Cloudinary)**: Generación de fondos por IA usando hasta 5 cuentas rotativas de Cloudinary.
-   **Studio Pro**: Editor rápido para Shorts de un solo clip con optimización de guion por IA.
-   **Galería Multimedia**: Gestión de vídeos, imágenes y música persistente.
-   **Seguridad 2FA**: Dashboard protegido con Google Authenticator.
-   **VozNarrativa Premium**: Integración con ElevenLabs con rotación de claves.
-   **Automatización con n8n**: Flujos listos para automatizar desde Reddit o RSS.

## 🛠️ Arquitectura

1.  **Shorts Engine (Python/FastAPI)**: Servidor de renderizado, orquestador de IA y Dashboard.
2.  **n8n Workflow**: El director de orquesta (ubicado en `/workflows`).

---

## 📦 Instalación y Despliegue (Dokploy)

Este proyecto está optimizado para **Dokploy** usando el archivo `docker-compose.yml` de la raíz del repo. Ese compose usa una imagen publicada en GHCR, así que Dokploy no necesita construir `python:3.12-slim` en el servidor.

### Flujo recomendado

1. El workflow de GitHub Actions construye la imagen.
2. La imagen se publica en `ghcr.io/koday75/publicador-short-youtube:latest`.
3. Dokploy solo hace `pull` y arranca el contenedor.

### Variables de entorno

Usa el archivo `.env.example` de la raíz como plantilla para tu `.env`.

### ⚙️ Centro de Configuración (Dashboard)

En lugar de depender solo de variables `.env`, ahora puedes configurar tus APIs directamente en el panel de **Ajustes**:
- **IA**: Groq, OpenAI, DeepSeek, OpenRouter.
- **Cloudinary**: Hasta 5 cuentas completas para generación de medios.

---

## 🎨 Cinema Storyboard (Edición Profesional)

El nuevo motor Cinema permite:
1. **Montaje por Escenas**: Define qué dice el locutor y qué se ve en cada tramo del video.
2. **Generación Dinámica**: Si no tienes fondo, la IA lo crea basándose en el guion de la escena.
3. **Posicionamiento de Texto**: Elige dónde poner el subtítulo (Arriba, Centro, Abajo) y su tamaño para un acabado cinemático.
4. **Transiciones**: Fundidos suaves entre escenas para un resultado profesional.

---

## 🤝 Contribuciones

Si tienes ideas para mejorar los prompts de la IA o el motor de renderizado, ¡abre un PR!

---
**Desarrollado con ❤️ para la comunidad de automatización.**
