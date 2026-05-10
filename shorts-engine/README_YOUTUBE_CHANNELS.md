# YouTube / Canales

Este módulo permite crear un canal interno dentro de la app, conectarlo a un canal de YouTube ya existente mediante OAuth 2.0 de Google y dejar preparado el sistema para subir vídeos con YouTube Data API v3.

## 1. Crear credenciales OAuth en Google Cloud

1. Entra en [Google Cloud Console](https://console.cloud.google.com/).
2. Crea o selecciona un proyecto.
3. Activa la API de **YouTube Data API v3**.
4. Configura la pantalla de consentimiento OAuth.
5. Crea un cliente OAuth de tipo **Web application**.
6. Añade la URL de callback exacta que usará la app.

## 2. Redirect URI

Configura como redirect URI:

```text
https://TU-DOMINIO/api/youtube/oauth/callback
```

Para entorno local, por ejemplo:

```text
http://localhost:8000/api/youtube/oauth/callback
```

## 3. Variables de entorno

Configura estas variables:

```bash
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
YOUTUBE_SCOPES=https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly openid email profile
YOUTUBE_TOKEN_ENCRYPTION_KEY=
```

Notas:

- `YOUTUBE_SCOPES` incluye subida de vídeos y lectura básica del canal autenticado.
- `YOUTUBE_TOKEN_ENCRYPTION_KEY` debe ser estable y secreta en producción.
- No guardes el `client_secret` ni los tokens en el frontend.

## 4. Flujo de conexión

1. Entra en **YouTube / Canales**.
2. Crea un canal interno.
3. Guarda la configuración.
4. Pulsa **Conectar con Google / YouTube**.
5. Google mostrará el consentimiento OAuth.
6. Tras autorizar, la app recibe el `code` en `/api/youtube/oauth/callback`.
7. La app intercambia el `code` por tokens.
8. La app consulta los datos reales del canal autenticado y guarda la conexión.

## 5. Probar conexión

Desde la pantalla de canales:

1. Selecciona un canal.
2. Pulsa **Probar conexión**.
3. La app verificará el access token.
4. Si el token está caducado, intentará renovarlo con el refresh token.

Resultado esperado:

- `Conexión correcta`
- `Token caducado, se ha renovado correctamente`
- `No se pudo conectar, vuelve a autorizar el canal`

## 6. Datos que guarda la app

La tabla `youtube_channels` almacena:

- Configuración interna del canal.
- Datos públicos del canal de YouTube.
- Tokens OAuth cifrados.
- Fecha de expiración del access token.
- Última prueba de conexión.
- Estado de conexión.

## 7. Subida de vídeos

El servicio interno ya deja preparada la función `uploadVideo(channelId, filePathOrStream, metadata)` para usar la YouTube Data API v3 con:

- `title`
- `description`
- `tags`
- `categoryId`
- `privacyStatus`
- `notifySubscribers`

No usa service accounts. La conexión es por OAuth 2.0 de usuario/canal.
