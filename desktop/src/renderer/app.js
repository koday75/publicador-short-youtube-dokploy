const STORAGE_KEY = "channelclip.desktop.config";

const state = {
  serverUrl: "",
  token: "",
  channels: [],
  selectedChannelId: null,
  jobs: [],
  selectedJobId: null,
  project: null,
  assets: [],
  activeSceneIndex: 0,
  selectedRenderFile: null
};

const els = {
  serverUrlInput: document.getElementById("serverUrlInput"),
  tokenInput: document.getElementById("tokenInput"),
  saveConnectionBtn: document.getElementById("saveConnectionBtn"),
  testConnectionBtn: document.getElementById("testConnectionBtn"),
  reloadChannelsBtn: document.getElementById("reloadChannelsBtn"),
  connectionStatus: document.getElementById("connectionStatus"),
  channelsList: document.getElementById("channelsList"),
  workspaceTitle: document.getElementById("workspaceTitle"),
  workspaceSubtitle: document.getElementById("workspaceSubtitle"),
  createJobBtn: document.getElementById("createJobBtn"),
  saveProjectBtn: document.getElementById("saveProjectBtn"),
  reloadJobsBtn: document.getElementById("reloadJobsBtn"),
  jobsList: document.getElementById("jobsList"),
  emptyEditor: document.getElementById("emptyEditor"),
  projectForm: document.getElementById("projectForm"),
  projectTitleInput: document.getElementById("projectTitleInput"),
  videoFormatInput: document.getElementById("videoFormatInput"),
  nicheInput: document.getElementById("nicheInput"),
  musicInput: document.getElementById("musicInput"),
  ttsEngineInput: document.getElementById("ttsEngineInput"),
  voiceInput: document.getElementById("voiceInput"),
  addSceneBtn: document.getElementById("addSceneBtn"),
  scenesList: document.getElementById("scenesList"),
  assetsCount: document.getElementById("assetsCount"),
  assetsList: document.getElementById("assetsList"),
  syncSummary: document.getElementById("syncSummary"),
  selectRenderBtn: document.getElementById("selectRenderBtn"),
  uploadRenderBtn: document.getElementById("uploadRenderBtn"),
  selectedRenderPath: document.getElementById("selectedRenderPath"),
  toast: document.getElementById("toast")
};

function normalizeServerUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function absoluteServerUrl(path) {
  const normalizedPath = String(path || "").trim();
  if (!normalizedPath) return "";
  if (/^https?:\/\//i.test(normalizedPath)) return normalizedPath;
  if (!state.serverUrl) return normalizedPath;
  return `${state.serverUrl}${normalizedPath.startsWith("/") ? normalizedPath : `/${normalizedPath}`}`;
}

function showToast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.style.borderColor = isError ? "rgba(255, 107, 107, 0.65)" : "rgba(70, 214, 181, 0.5)";
  els.toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => els.toast.classList.add("hidden"), 3800);
}

function saveLocalConfig() {
  state.serverUrl = normalizeServerUrl(els.serverUrlInput.value);
  state.token = els.tokenInput.value.trim();
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ serverUrl: state.serverUrl, token: state.token }));
}

function loadLocalConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    state.serverUrl = normalizeServerUrl(parsed.serverUrl);
    state.token = parsed.token || "";
    els.serverUrlInput.value = state.serverUrl;
    els.tokenInput.value = state.token;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
}

async function apiFetch(path, options = {}) {
  if (!state.serverUrl || !state.token) {
    throw new Error("Configura la URL del servidor y el token de escritorio.");
  }

  const headers = {
    "X-Desktop-Token": state.token,
    "X-API-Key": state.token,
    "Authorization": `Bearer ${state.token}`,
    ...(options.headers || {})
  };

  const response = await fetch(`${state.serverUrl}${path}`, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload;
    if (response.status === 401) {
      throw new Error(`${detail || "Cliente no autorizado"}. Prueba /api/desktop/auth-info para comprobar si el servidor ve DESKTOP_API_TOKEN.`);
    }
    throw new Error(detail || `Error HTTP ${response.status}`);
  }

  return payload;
}

function statusLabel(status) {
  const labels = {
    draft: "Borrador",
    desktop_draft: "Borrador Windows",
    editing_desktop: "Editando en Windows",
    rendered: "Renderizado",
    rendered_local: "Render local",
    ready_to_publish: "Listo para publicar",
    published: "Publicado",
    published_youtube: "Publicado en YouTube"
  };
  return labels[status] || status || "Sin estado";
}

function selectedChannel() {
  return state.channels.find((channel) => Number(channel.id) === Number(state.selectedChannelId));
}

function renderChannels() {
  if (!state.channels.length) {
    els.channelsList.className = "list empty";
    els.channelsList.textContent = "No hay canales disponibles.";
    return;
  }

  els.channelsList.className = "list";
  els.channelsList.innerHTML = state.channels.map((channel) => {
    const active = Number(channel.id) === Number(state.selectedChannelId) ? " active" : "";
    const connected = channel.is_connected ? "Conectado" : "Pendiente";
    return `
      <article class="channel-card${active}" data-channel-id="${channel.id}">
        <div class="card-title">
          <span>${escapeHtml(channel.name || channel.youtube_channel_title || `Canal ${channel.id}`)}</span>
          <span class="badge ${channel.is_connected ? "" : "muted"}">${connected}</span>
        </div>
        <p class="hint">${escapeHtml(channel.description || channel.youtube_channel_title || "Sin descripcion")}</p>
      </article>
    `;
  }).join("");
}

function renderJobs() {
  if (!state.selectedChannelId) {
    els.jobsList.className = "list empty";
    els.jobsList.textContent = "Selecciona un canal para ver sus trabajos.";
    return;
  }

  if (!state.jobs.length) {
    els.jobsList.className = "list empty";
    els.jobsList.textContent = "Este canal todavia no tiene trabajos.";
    return;
  }

  els.jobsList.className = "list";
  els.jobsList.innerHTML = state.jobs.map((job) => {
    const jobId = job.job_id || job.id;
    const active = jobId === state.selectedJobId ? " active" : "";
    return `
      <article class="job-card${active}" data-job-id="${escapeHtml(jobId)}">
        <div class="card-title">
          <span>${escapeHtml(job.title || jobId)}</span>
          <span class="badge muted">${statusLabel(job.status)}</span>
        </div>
        <p class="hint">${escapeHtml(job.created_at || "")}</p>
      </article>
    `;
  }).join("");
}

function renderWorkspaceHeader() {
  const channel = selectedChannel();
  if (!channel) {
    els.workspaceTitle.textContent = "Selecciona un canal";
    els.workspaceSubtitle.textContent = "Crea o abre trabajos y sincronizalos con la app web.";
    els.createJobBtn.disabled = true;
    els.reloadJobsBtn.disabled = true;
    return;
  }

  els.workspaceTitle.textContent = channel.name || channel.youtube_channel_title || `Canal ${channel.id}`;
  els.workspaceSubtitle.textContent = channel.description || "Workspace local conectado al servidor.";
  els.createJobBtn.disabled = false;
  els.reloadJobsBtn.disabled = false;
}

function renderProject() {
  if (!state.project) {
    els.emptyEditor.classList.remove("hidden");
    els.projectForm.classList.add("hidden");
    els.saveProjectBtn.disabled = true;
    els.selectRenderBtn.disabled = true;
    els.uploadRenderBtn.disabled = true;
    els.syncSummary.innerHTML = "<strong>Sin trabajo abierto</strong><span>La app guardara los cambios en el servidor cuando pulses guardar.</span>";
    return;
  }

  const project = state.project;
  els.emptyEditor.classList.add("hidden");
  els.projectForm.classList.remove("hidden");
  els.saveProjectBtn.disabled = false;
  els.selectRenderBtn.disabled = false;
  els.uploadRenderBtn.disabled = !state.selectedRenderFile;
  els.projectTitleInput.value = project.title || "";
  els.videoFormatInput.value = project.video_format || "vertical";
  els.nicheInput.value = project.niche || "default";
  els.musicInput.value = project.audio?.music_filename || project.music_filename || "";
  els.ttsEngineInput.value = project.audio?.tts_engine || project.tts_engine || "";
  els.voiceInput.value = project.audio?.voice_id || project.voice_id || "";
  els.syncSummary.innerHTML = `
    <strong>${escapeHtml(project.title || project.job_id || project.id)}</strong>
    <span>Estado: ${statusLabel(project.status)}</span>
    <span>Trabajo: ${escapeHtml(project.job_id || project.id)}</span>
  `;
  renderScenes();
  renderAssets();
}

function renderScenes() {
  const scenes = Array.isArray(state.project?.scenes) ? state.project.scenes : [];
  if (!scenes.length) {
    els.scenesList.innerHTML = '<div class="empty">Este trabajo no tiene escenas todavia.</div>';
    return;
  }

  els.scenesList.innerHTML = scenes.map((scene, index) => `
    <article class="scene-card ${index === state.activeSceneIndex ? "active" : ""}" data-scene-index="${index}">
      <div class="scene-index">${index + 1}</div>
      <div class="scene-fields">
        <label>
          Texto de la escena
          <textarea data-field="text">${escapeHtml(scene.text || "")}</textarea>
        </label>
        <div class="scene-options">
          <label>
            Asset del servidor
            <select data-field="media_asset_choice">
              ${assetOptions(scene.media_filename || "")}
            </select>
          </label>
          <label>
            Archivo visual manual
            <input data-field="media_filename" type="text" value="${escapeHtml(scene.media_filename || "")}" placeholder="imagen.png o video.mp4" />
          </label>
          <label>
            Posicion subtitulo
            <select data-field="subtitle_pos">
              ${subtitleOptions(scene.subtitle_pos)}
            </select>
          </label>
          <label>
            Tamano subtitulo
            <input data-field="subtitle_size" type="number" min="12" max="120" value="${Number(scene.subtitle_size || 48)}" />
          </label>
        </div>
        <div class="row">
          <label class="check-row">
            <input data-field="show_text" type="checkbox" ${scene.show_text === false ? "" : "checked"} />
            Mostrar texto en pantalla
          </label>
          <button type="button" class="danger" data-action="delete-scene">Eliminar escena</button>
        </div>
      </div>
    </article>
  `).join("");
}

function assetOptions(selectedFilename) {
  const options = [
    `<option value="">Elegir asset del servidor...</option>`,
    ...state.assets.map((asset) => {
      const label = `${asset.original_name || asset.filename} (${asset.file_type || "media"})`;
      return `<option value="${escapeHtml(asset.filename || "")}" ${String(asset.filename || "") === String(selectedFilename || "") ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
  ];
  return options.join("");
}

function renderAssets() {
  if (!els.assetsList || !els.assetsCount) return;

  const assets = state.assets || [];
  els.assetsCount.textContent = `${assets.length} archivo${assets.length === 1 ? "" : "s"}`;

  if (!state.selectedChannelId) {
    els.assetsList.className = "assets-list empty";
    els.assetsList.textContent = "Selecciona un canal para ver sus assets.";
    return;
  }

  if (!assets.length) {
    els.assetsList.className = "assets-list empty";
    els.assetsList.textContent = "Aun no hay assets en este canal.";
    return;
  }

  els.assetsList.className = "assets-list";
  els.assetsList.innerHTML = assets.map((asset) => {
    const url = absoluteServerUrl(asset.url || "");
    const fileType = String(asset.file_type || "other").toLowerCase();
    const preview = fileType === "image"
      ? `<img src="${escapeHtml(url)}" alt="${escapeHtml(asset.original_name || asset.filename || "asset")}" loading="lazy" />`
      : fileType === "video"
        ? `<video src="${escapeHtml(url)}" muted playsinline preload="metadata"></video>`
        : `<div class="asset-icon">${fileType === "audio" ? "♪" : "◫"}</div>`;

    return `
      <article class="asset-card" data-asset-filename="${escapeHtml(asset.filename || "")}">
        <div class="asset-preview">${preview}</div>
        <div class="asset-meta">
          <strong>${escapeHtml(asset.original_name || asset.filename || "Asset")}</strong>
          <span>${escapeHtml((asset.file_type || "other").toUpperCase())} · ${escapeHtml(asset.created_at || "")}</span>
        </div>
        <div class="asset-actions">
          <button type="button" class="ghost" data-action="use-asset">Usar en escena</button>
          <button type="button" class="ghost" data-action="copy-asset">Copiar nombre</button>
        </div>
      </article>
    `;
  }).join("");
}

function subtitleOptions(selectedValue) {
  const value = Number(selectedValue ?? 5);
  return [
    [3, "Arriba"],
    [5, "Centro"],
    [7, "Abajo"]
  ].map(([optionValue, label]) => (
    `<option value="${optionValue}" ${optionValue === value ? "selected" : ""}>${label}</option>`
  )).join("");
}

function readProjectFromForm() {
  if (!state.project) return null;

  const scenes = Array.from(els.scenesList.querySelectorAll(".scene-card")).map((card) => {
    const get = (field) => card.querySelector(`[data-field="${field}"]`);
    return {
      text: get("text")?.value || "",
      media_filename: get("media_filename")?.value || "",
      subtitle_pos: Number(get("subtitle_pos")?.value || 5),
      subtitle_size: Number(get("subtitle_size")?.value || 48),
      show_text: Boolean(get("show_text")?.checked),
      transition_in: "fade",
      transition_out: "fade",
      image_effect: "zoom_in"
    };
  });

  return {
    channel_id: state.selectedChannelId,
    title: els.projectTitleInput.value.trim() || "Trabajo sin titulo",
    niche: els.nicheInput.value.trim() || "default",
    video_format: els.videoFormatInput.value || "vertical",
    status: state.project.status || "editing_desktop",
    music_filename: els.musicInput.value.trim(),
    tts_engine: els.ttsEngineInput.value.trim(),
    voice_id: els.voiceInput.value.trim(),
    scenes
  };
}

function newScene() {
  return {
    text: "",
    media_filename: "",
    subtitle_pos: 5,
    subtitle_size: 48,
    show_text: true,
    transition_in: "fade",
    transition_out: "fade",
    image_effect: "zoom_in"
  };
}

function setActiveSceneIndex(index) {
  const sceneCount = state.project?.scenes?.length || 0;
  if (!sceneCount) {
    state.activeSceneIndex = 0;
    return;
  }
  state.activeSceneIndex = Math.max(0, Math.min(Number(index) || 0, sceneCount - 1));
}

function applyAssetToActiveScene(filename) {
  const sceneCard = els.scenesList.querySelector(`[data-scene-index="${state.activeSceneIndex}"]`);
  if (!sceneCard) return false;
  const filenameInput = sceneCard.querySelector('[data-field="media_filename"]');
  const assetSelect = sceneCard.querySelector('[data-field="media_asset_choice"]');
  if (filenameInput) filenameInput.value = filename;
  if (assetSelect) assetSelect.value = filename;
  return true;
}

async function loadChannels() {
  els.connectionStatus.textContent = "Cargando canales...";
  const payload = await apiFetch("/api/desktop/channels");
  state.channels = payload.channels || [];
  renderChannels();
  els.connectionStatus.textContent = `Conexion correcta. ${state.channels.length} canales cargados.`;
  renderWorkspaceHeader();
}

async function selectChannel(channelId) {
  state.selectedChannelId = Number(channelId);
  state.selectedJobId = null;
  state.project = null;
  state.selectedRenderFile = null;
  state.assets = [];
  renderChannels();
  renderWorkspaceHeader();
  renderProject();
  await loadJobs();
  await loadAssets();
}

async function loadJobs() {
  if (!state.selectedChannelId) return;
  els.jobsList.className = "list empty";
  els.jobsList.textContent = "Cargando trabajos...";
  const payload = await apiFetch(`/api/desktop/jobs?channel_id=${encodeURIComponent(state.selectedChannelId)}`);
  state.jobs = payload.jobs || [];
  renderJobs();
}

async function createJob() {
  const channel = selectedChannel();
  if (!channel) return;

  const now = new Date();
  const stamp = now.toISOString().replace(/[-:]/g, "").slice(0, 13);
  const title = `Trabajo ${channel.internal_name || channel.youtube_channel_title || channel.id} ${stamp}`;

  const payload = await apiFetch("/api/desktop/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      channel_id: channel.id,
      title,
      niche: "default",
      video_format: "vertical",
      status: "desktop_draft",
      scenes: [newScene()]
    })
  });

  await loadJobs();
  await openJob(payload.project.job_id);
  showToast("Trabajo creado correctamente.");
}

async function openJob(jobId) {
  const payload = await apiFetch(`/api/desktop/jobs/${encodeURIComponent(jobId)}/project`);
  state.selectedJobId = jobId;
  state.project = payload.project;
  state.selectedRenderFile = null;
  state.activeSceneIndex = 0;
  els.selectedRenderPath.textContent = "Ningun video seleccionado.";
  renderJobs();
  renderProject();
  await loadAssets(payload.project?.channel_id || state.selectedChannelId);
}

async function saveProject() {
  const body = readProjectFromForm();
  if (!body || !state.selectedJobId) return;

  const payload = await apiFetch(`/api/desktop/jobs/${encodeURIComponent(state.selectedJobId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  state.project = payload.project;
  await loadJobs();
  renderProject();
  showToast("Trabajo guardado en el servidor.");
}

async function loadAssets(channelId = state.selectedChannelId) {
  if (!channelId) {
    state.assets = [];
    renderAssets();
    return;
  }

  try {
    const payload = await apiFetch(`/api/desktop/channels/${encodeURIComponent(channelId)}/media?limit=200`);
    state.assets = payload.assets || [];
  } catch (error) {
    if (!String(error.message || "").includes("404")) {
      throw error;
    }
    state.assets = [];
  }
  renderAssets();
  renderScenes();
}

async function selectRenderFile() {
  const filePath = await window.channelClipDesktop.selectVideoFile();
  if (!filePath) return;
  state.selectedRenderFile = filePath;
  els.selectedRenderPath.textContent = filePath;
  els.uploadRenderBtn.disabled = false;
}

async function uploadRender() {
  if (!state.selectedJobId || !state.selectedRenderFile) return;

  const file = await fileFromPath(state.selectedRenderFile);
  const formData = new FormData();
  formData.append("file", file);

  const payload = await apiFetch(`/api/desktop/jobs/${encodeURIComponent(state.selectedJobId)}/render`, {
    method: "POST",
    body: formData
  });

  await openJob(payload.job_id);
  await loadJobs();
  showToast("Video subido. Ya queda listo para publicar desde la web.");
}

async function fileFromPath(filePath) {
  const fileData = await window.channelClipDesktop.readFileAsBase64(filePath);
  const binary = atob(fileData.base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], fileData.name || "render.mp4", { type: "video/mp4" });
}

function addScene() {
  if (!state.project) return;
  state.project.scenes = Array.isArray(state.project.scenes) ? state.project.scenes : [];
  state.project.scenes.push(newScene());
  setActiveSceneIndex(state.project.scenes.length - 1);
  renderScenes();
}

function deleteScene(index) {
  if (!state.project?.scenes) return;
  state.project.scenes.splice(index, 1);
  setActiveSceneIndex(Math.min(state.activeSceneIndex, (state.project.scenes.length || 1) - 1));
  renderScenes();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bindEvents() {
  els.saveConnectionBtn.addEventListener("click", () => {
    saveLocalConfig();
    showToast("Conexion guardada.");
  });

  els.testConnectionBtn.addEventListener("click", async () => {
    try {
      saveLocalConfig();
      await loadChannels();
      showToast("Conexion comprobada correctamente.");
    } catch (error) {
      try {
        const infoResponse = await fetch(`${state.serverUrl}/api/desktop/auth-info`);
        const info = await infoResponse.json();
        const configured = [
          info.desktop_api_token_configured ? "DESKTOP_API_TOKEN" : "",
          info.desktop_api_key_configured ? "DESKTOP_API_KEY" : "",
          info.x_api_key_configured ? "X_API_KEY" : "",
          info.dashboard_password_fallback_available ? "DASHBOARD_PASSWORD" : ""
        ].filter(Boolean).join(", ") || "ninguna";
        showToast(`${error.message} Variables detectadas en servidor: ${configured}.`, true);
      } catch {
        showToast(error.message, true);
      }
    }
  });

  els.reloadChannelsBtn.addEventListener("click", async () => {
    try {
      saveLocalConfig();
      await loadChannels();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.channelsList.addEventListener("click", async (event) => {
    const card = event.target.closest("[data-channel-id]");
    if (!card) return;
    try {
      await selectChannel(card.dataset.channelId);
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.reloadJobsBtn.addEventListener("click", async () => {
    try {
      await loadJobs();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.jobsList.addEventListener("click", async (event) => {
    const card = event.target.closest("[data-job-id]");
    if (!card) return;
    try {
      await openJob(card.dataset.jobId);
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.createJobBtn.addEventListener("click", async () => {
    try {
      await createJob();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.saveProjectBtn.addEventListener("click", async () => {
    try {
      await saveProject();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.addSceneBtn.addEventListener("click", addScene);

  els.scenesList.addEventListener("click", (event) => {
    const button = event.target.closest('[data-action="delete-scene"]');
    if (!button) return;
    const card = event.target.closest("[data-scene-index]");
    deleteScene(Number(card.dataset.sceneIndex));
  });

  els.scenesList.addEventListener("focusin", (event) => {
    const card = event.target.closest("[data-scene-index]");
    if (card) {
      setActiveSceneIndex(card.dataset.sceneIndex);
      renderScenes();
    }
  });

  els.scenesList.addEventListener("change", (event) => {
    const sceneCard = event.target.closest("[data-scene-index]");
    if (!sceneCard) return;
    const sceneIndex = Number(sceneCard.dataset.sceneIndex);
    if (event.target.matches('[data-field="media_asset_choice"]')) {
      const filenameInput = sceneCard.querySelector('[data-field="media_filename"]');
      if (filenameInput) {
        filenameInput.value = event.target.value || filenameInput.value || "";
      }
    }
    setActiveSceneIndex(sceneIndex);
  });

  els.scenesList.addEventListener("click", (event) => {
    const sceneCard = event.target.closest("[data-scene-index]");
    if (sceneCard) {
      setActiveSceneIndex(sceneCard.dataset.sceneIndex);
      renderScenes();
    }
  });

  els.assetsList.addEventListener("click", async (event) => {
    const card = event.target.closest("[data-asset-filename]");
    if (!card) return;
    const filename = card.dataset.assetFilename;
    if (!filename) return;

    const copyButton = event.target.closest('[data-action="copy-asset"]');
    const useButton = event.target.closest('[data-action="use-asset"]');

    if (copyButton) {
      await navigator.clipboard.writeText(filename);
      showToast("Nombre del asset copiado.");
      return;
    }

    if (useButton || card) {
      if (applyAssetToActiveScene(filename)) {
        showToast(`Asset usado en la escena ${state.activeSceneIndex + 1}.`);
      }
    }
  });

  els.selectRenderBtn.addEventListener("click", async () => {
    try {
      await selectRenderFile();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  els.uploadRenderBtn.addEventListener("click", async () => {
    try {
      await uploadRender();
    } catch (error) {
      showToast(error.message, true);
    }
  });
}

loadLocalConfig();
bindEvents();
renderWorkspaceHeader();
renderProject();

if (state.serverUrl && state.token) {
  loadChannels().catch((error) => {
    els.connectionStatus.textContent = "No se pudo conectar automaticamente.";
    showToast(error.message, true);
  });
}
