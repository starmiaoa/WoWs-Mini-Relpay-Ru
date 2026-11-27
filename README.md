# WoWs-Mini-Relpay-Ru

**简体中文** | [繁體中文](docs/README.zh-TW.md) | [Русский](docs/README.ru.md) | [English](docs/README.en.md)

专门给俄服《战舰世界》（**Mir Korabley / Lesta**，`.korablireplay`）写的小地图回放渲染工具。  

![渲染效果预览](docs/preview.gif)

---

## 如何使用

1. 去右侧 [Releases](https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru/releases) 下载适合自己的 Windows 版本（提供三种选择）：
   - **Windows 安装包（推荐）**：`WoWs-Mini-Relpay-Ru-Setup-win64.exe`，一路下一步即可完成安装，自动创建开始菜单与桌面快捷方式。
   - **免安装绿色便携包**：`WoWs-Mini-Relpay-Ru-win64.7z` 或 `WoWs-Mini-Relpay-Ru-win64.zip`，**先解压**到任意文件夹，再双击 `WoWs-Mini-Relpay-Ru.exe`。
2. 打开后会自动找到你装的俄服正式服 / PT 服并列出 `replays` 里的 `.korablireplay`（如果没有找到，点一下右上角「选择目录」自己指一下就行）。
3. 勾选你想生成的对局，或者把 `.korablireplay` 文件直接拖进窗口里。
4. 右边选好 16:9 标准分辨率（`720p` / 默认 `1K 1920×1080` / `2K` / `4K`）和帧率（默认 **`25fps`**，可选 `25` / `30` / `60` fps；帧率与倍速解耦，调高只提升画面流畅度，不会加快战局进度），点 **「开始生成」**。
5. 等进度条走完，程序会自动打开这次按时间建好的输出文件夹，视频都在里面。

> 首次打开时程序会在后台顺手从你本地游戏目录里提取一次地图、图标和汉化词库（大概几秒钟），以后就不用再等了。  

---

## 游戏、汉化 mod 或软件更新了怎么办？

不用折腾重装，大部分更新在软件里点一下就能搞定：

- **游戏大版本更新后**：打开软件 → 切到 **「设置」** → 点 **「重新同步当前客户端数据」**（几秒完成）。
- **只是更新了汉化 mod 船名**：切到 **「设置」** → 点 **「仅更新汉化词库」**（1 秒完成）。
- **软件本身有新版本时**：切到 **「关于」** → 点 **「检查更新」**，检测到新版后直接点 **「快速拉取更新」** 即可自动下载并应用最新 GitHub Release（保留本地 `data/` 缓存与 `output/` 视频）。

如果是非常少见的大改导致协议解析报错，可以参考 [UPDATING.md](UPDATING.md) 里的排查说明，或者直接提 Issue 并附上一局新版 `.korablireplay`。

---

## 开发者源码运行与命令行

如果你想改代码或者用命令行批量跑，推荐使用 [uv](https://docs.astral.sh/uv/)（依赖里已经自带独立 FFmpeg，不需要配系统环境变量）：

```powershell
git clone https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru.git
cd WoWs-Mini-Relpay-Ru
uv sync --extra dev

# 1. 启动图形界面
.\.venv\Scripts\korabli-minimap.exe gui

# 2. 从本地俄服客户端同步数据
.\.venv\Scripts\korabli-minimap.exe sync --game-dir "D:\Games\Korabli"

# 3. 命令行直接渲染单个回放
.\.venv\Scripts\korabli-minimap.exe render "D:\Games\Korabli\replays\xxx.korablireplay" `
  -o "output\battle.mp4" --resolution 1920x1080 --fps 25 --speed 1.0 --lang zh_CN

# 4. 只解析查看战报信息（不出视频）
.\.venv\Scripts\korabli-minimap.exe inspect "D:\Games\Korabli\replays\xxx.korablireplay"

# 5. 打包免环境绿色版 EXE 与发布压缩包（ZIP / 7z）
.\.venv\Scripts\python.exe scripts\build_release.py

# 6. 运行自动化测试
.\.venv\Scripts\pytest.exe -v
```

---

## 致谢

- 画面布局与渲染思路参考了 [WoWs-Builder-Team/minimap_renderer](https://github.com/WoWs-Builder-Team/minimap_renderer)
- 侧边舰队面板风格参考了 BattleFrame 的 Tactical-Tech 模组
- 中文船名与字体默认读取玩家本地安装的汉化 mod（如 澪刻 LocalizedKorabli）
