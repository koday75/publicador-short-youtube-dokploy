const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("channelClipDesktop", {
  selectVideoFile: () => ipcRenderer.invoke("dialog:selectVideo"),
  readFileAsBase64: (filePath) => ipcRenderer.invoke("file:readAsBase64", filePath)
});
