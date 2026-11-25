# 版本更新与维护说明

俄服《战舰世界》（Mir Korabley）每隔几周就会发小补丁或者大版本。这个项目一开始就是按「尽量不用改代码就能跟着游戏一起升级」来做的，平时遇到更新按下面几种情况处理就行。

---

## 1. 平时更新（绝大多数时候不用改代码）

### 游戏发了新版本（包括切到 PT 测试服）
游戏更新完之后，直接二选一：

- **用图形界面**：打开程序 → 切到 **「设置」** → 点 **「重新同步当前客户端数据」**（其实直接开渲染也会自动同步）。
- **用命令行**：
  ```powershell
  .\.venv\Scripts\korabli-minimap.exe sync --game-dir "D:\Games\Korabli"
  ```

同步完的数据会单独存进 `data/versions/<版本号>/`，**不会覆盖旧版本**，所以你硬盘里以前打的老回放依然能正常渲染。

### 只是汉化 mod 更新了（比如补了新船翻译）
不用重扫地图和图标，直接极速刷新一下词库就行（大概 1 秒）：

- **图形界面**：「设置」→「仅更新汉化词库」
- **命令行**：
  ```powershell
  .\.venv\Scripts\korabli-minimap.exe sync --game-dir "D:\Games\Korabli" --i18n-only
  ```

### 想补同步 `bin/` 里残留的旧版本客户端
如果 `D:\Games\Korabli\bin\` 下面还留着上一版的 build 目录，可以指定 `--build` 把旧版也同步进来，方便渲染上个版本的录像：

```powershell
.\.venv\Scripts\korabli-minimap.exe sync --game-dir "D:\Games\Korabli" --build 8870710
.\.venv\Scripts\korabli-minimap.exe versions
```

---

## 2. 万一遇到大版本改了底层协议怎么办？

如果某次大更新后，点生成发现报错、船不动了、或者比分/烟雾没出来，先用 `inspect` 跑一下新版本的录像看看统计数据：

```powershell
.\.venv\Scripts\korabli-minimap.exe inspect "path\to\new_battle.korablireplay"
```

重点盯住输出里的 `prop_errors`、`nested_errors` 和 `unknown_packet_counts`：

- **数据包编号变了**（`unknown_packet_counts` 里突然多出一堆大数，船不移动或比分一直是 0）  
  去 `src/korabli_minimap/parser/packet_map.py`，按新版本号补一份新的 `PacketIdMap` 就行，不要覆盖旧版本的映射。
- **实体方法改名 / 参数结构变了**（例如烟雾、聊天、伤害包解不出来）  
  先翻一下新同步出来的 `data/versions/<新版本>/entity_defs/*.def`，确认字段变化后改 `src/korabli_minimap/parser/replay_parser.py`。
- **名单字段下标变了**（比如玩家名、军团名或者阵营错位）  
  检查 `0x1d` 包里解出来的字典键位，对应更新 `replay_parser.py` 里的 `_extract_roster_from_0x1d`。
- **新增了击沉图标或新消耗品**  
  补到 `src/korabli_minimap/domain/battle_state.py` 的 `DEATH_REASON_ZH` 或 `on_consumable_used` 里即可。
- **纯调界面配色 / 布局 / 文字**  
  只改 `src/korabli_minimap/config.py`、`src/korabli_minimap/i18n.py` 或 `src/korabli_minimap/renderer/layers.py`，完全不会碰到底层解析。

---

## 3. 改完之后的自检与发版

每次适配完大版本或者修完功能，顺手跑两步确认没把旧逻辑搞坏：

```powershell
# 跑单元测试
.\.venv\Scripts\pytest.exe -v

# 用一局新版真实录像抽几张关键帧肉眼看一眼
.\.venv\Scripts\korabli-minimap.exe render "path\to\new_battle.korablireplay" --keyframes-dir "output\check"
```

确认无误后：
- 本地打包绿色版直接双击 **`打包EXE.bat`**（会在 `dist/` 生成 `WoWs-Mini-Relpay-Ru-win64.zip`）；
- 或者直接打个 Git 标签推到 GitHub（例如 `git tag v1.0.1 && git push origin v1.0.1`），GitHub Actions 会自动在云端跑测试、打包并发布到 Releases。
