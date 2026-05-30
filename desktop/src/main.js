const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const fs = require("fs/promises");
const path = require("path");

const iconPath = path.join(__dirname, "..", "..", "icono.ico");

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 1100,
    minHeight: 720,
    title: "ChannelClip Studio",
    icon: iconPath,
    backgroundColor: "#0f141b",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  ipcMain.handle("dialog:selectVideo", async () => {
    const result = await dialog.showOpenDialog({
      title: "Seleccionar video renderizado",
      properties: ["openFile"],
      filters: [
        { name: "Videos", extensions: ["mp4", "mov", "mkv", "webm"] },
        { name: "Todos los archivos", extensions: ["*"] }
      ]
    });

    if (result.canceled || !result.filePaths.length) {
      return null;
    }

    return result.filePaths[0];
  });

  ipcMain.handle("file:readAsBase64", async (_event, filePath) => {
    const buffer = await fs.readFile(filePath);
    return {
      name: path.basename(filePath),
      base64: buffer.toString("base64")
    };
  });

  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
