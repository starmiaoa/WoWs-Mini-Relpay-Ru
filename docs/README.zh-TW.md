# WoWs-Mini-Relpay-Ru

[简体中文](../README.md) | **繁體中文** | [Русский](README.ru.md) | [English](README.en.md)

專為俄服《戰艦世界》（**Mir Korabley / Lesta**，`.korablireplay`）打造的小地圖重播渲染工具。  
整體扁平化風格參考自 [WoWs-Builder-Team/minimap_renderer](https://github.com/WoWs-Builder-Team/minimap_renderer)，專注俄服客戶端協議與資源，並補齊了日常覆盤最實用的幾個功能：

- **地圖煙霧渲染**：完整還原煙幕施放與消散過程，半透明融合輪廓不遮擋煙內艦船。
- **滅點船名顯示**：敵艦滅點後保留空心最後已知位置圖示，並在旁邊清楚標示船名。
- **自動讀取中文化 mod**：依照客戶端真實掛載順序讀取你安裝的本地化 mod（如 澪刻 LocalizedKorabli）與官方原版圖示，新船與超戰都能正確顯示中文名稱。
- **扁平長條血條與聊天區**：側邊面板參考 Tactical-Tech 風格，不使用船隻剪影，改以 100%-70% 綠 / 70%-30% 黃 / 30%-0% 紅的連續長條呈現，沉沒船隻自動下移至列表底部；小地圖下方內建局內聊天區。
- **免環境桌面介面**：提供綠色免安裝 EXE，自動偵測本機正式服與 PT 測試服目錄，支援直接拖入 `.korablireplay` 檔案批次輸出，完成後依時間建立資料夾並自動開啟。
- **多語言與跟隨系統主題**：介面與渲染出的影片皆支援 **簡體中文 / 繁體中文 / 俄語 / 英語**，預設自動跟隨系統語言與深淺色主題（非目標語言自動切換英語）。

![渲染效果預覽](preview.gif)

---

## 快速上手（免安裝 Python / FFmpeg）

1. 前往 [Releases](https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru/releases) 下載 `WoWs-Mini-Relpay-Ru-win64.zip`。
2. **解壓縮**到任意資料夾後，直接雙擊 `WoWs-Mini-Relpay-Ru.exe`。
3. 程式會自動找到本機的俄服正式服 / PT 服並列出 `replays` 中的 `.korablireplay` 重播（也可手動「選擇目錄」）。
4. 勾選要產生的場次，或直接將 `.korablireplay` 檔案拖進視窗。
5. 在右側選好解析度（預設 `1920×1080`）與影格率（預設 `30fps`），點擊 **「開始產生」**。
6. 渲染完成後會自動開啟本次以時間命名的輸出資料夾。

> 遊戲更新或中文化 mod 更新後，只需到 **「設定」** 分頁點一下「重新同步目前客戶端資料」或「僅更新中文化詞庫」即可適配新版本，詳情請看 [UPDATING.md](../UPDATING.md)。

---

## 開發者原始碼執行

```powershell
git clone https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru.git
cd WoWs-Mini-Relpay-Ru
uv sync --extra dev

# 啟動圖形介面
.\.venv\Scripts\korabli-minimap.exe gui

# 命令列渲染（繁體中文）
.\.venv\Scripts\korabli-minimap.exe render "D:\Games\Korabli\replays\xxx.korablireplay" --lang zh_TW

# 打包綠色版 EXE
.\.venv\Scripts\python.exe scripts\build_release.py
```
