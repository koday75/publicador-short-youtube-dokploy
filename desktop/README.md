# ChannelClip Studio para Windows

Primera version de la aplicacion de escritorio para crear y editar trabajos desde Windows, sincronizando con la app web.

## Requisitos

- Node.js 20 o superior.
- El servidor web desplegado y accesible.
- Un token configurado en el servidor con `DESKTOP_API_TOKEN`.

Si el servidor no tiene `DESKTOP_API_TOKEN`, aceptara temporalmente la misma clave que `DASHBOARD_PASSWORD`, pero es mejor usar un token propio.

## Ejecutar en desarrollo

```powershell
cd C:\Proyectos\short-youtube\desktop
npm install
npm run dev
```

Al abrir la app:

1. Escribe la URL del servidor, por ejemplo `https://channelclip.estrellitastudio.es`.
2. Escribe el token de escritorio.
3. Pulsa `Guardar conexion`.
4. Carga los canales y abre un trabajo.

## Que hace esta primera version

- Conecta con el servidor mediante la API de escritorio.
- Lista canales.
- Lista trabajos por canal.
- Crea trabajos nuevos asociados a un canal.
- Abre trabajos existentes.
- Edita titulo, nicho, formato, musica, motor TTS, voz y escenas basicas.
- Guarda el proyecto en el servidor.
- Sube un video renderizado para dejarlo listo para publicar desde la web.

## Siguiente fase recomendada

- Timeline visual con pistas de voz, musica, subtitulos, imagenes y video.
- Previsualizacion local.
- Render local con FFmpeg.
- Sincronizacion incremental de assets.
- Cache local para trabajar sin conexion y subir despues.
