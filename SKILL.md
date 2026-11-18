---
name: h3-studio-colab
description: 使用 Google Colab CLI 运行纯血 MiniMax H3 与 WepeNerd H3 Ripple 多模态工作流。提供四条标准创作流水线：爆款视频两步确认式复刻（先出关键帧对比图确认形象、再 6 步扩散成片）、AI 原创多镜头导演、单件素材生成（图/视频/配音）与免 GPU 音字后期合成。
---

# H3 Studio Colab · 纯血 MiniMax H3 + WepeNerd Ripple 工作流

先读取 `README.md` 与 `acceptance/功能验收.json`。所有操作统一使用单一入口 `python scripts/h3studio.py <子命令>`。

## 四大标准工作流（按需求直选，每条均有固定节奏）

### 工作流 1：🔥 爆款视频复刻（已有视频 → 换人 / 换商品 / 换画风 / 改口播字幕）

- **1A. 同镜改版（黄金默认：保原片背景与运镜，换人物/换商品/换场景）——必须走「两步确认式」：**
  1. **Step 1 生成关键帧预览并停下确认**：运行 `python scripts/h3studio.py remake-anchors request.json --gpu L4 --allow-unverified`。远端自动切段、抽取各镜头首/尾锚点帧，用 `P-H3-Image` + `QwenEditAlignComposite`（仿射对齐并逐像素锁死未改动背景）生成改后关键帧与 `原帧 | 改后帧` 左右对比图（`*_compare.png`）回传本机。**先把对比图展示给用户确认形象无误**（单张不满意加 `--only ID --session NAME` 秒级重抽，也可手动修图覆盖 `anchors/*.png`）。
  2. **Step 2 确认后扩散全片并合片**：用户确认后，运行 `python scripts/h3studio.py remake-render request.json --session NAME --confirmed --allow-unverified --output final.mp4`。自动复用热机中的 `FL2VA` 底模，通过 WepeNerd `H3 Ripple` **6 步 LCM** 将关键帧改动扩散至整段视频，串联新配音/字幕并拼接成片（未完成 Step 1 直接跑 Step 2 会被自动拦截保护点数）。
- **1B. 跨画风 / 跨物种重制（如真人爆款 → 2D 拟人猫动画大片）**：
  - 在 `request.json` 中设 `"mode": "recreate"`，无需跑 Step 1，直接运行 `remake-render`：自动将相邻快切合并至 `6~9.4s` 黄金段、生成 `640p` 静音参考代理提速 4 倍，走 `Ref2VA` 重制并合片。

### 工作流 2：🎬 原创多镜头短片（剧本/想法 → 完整故事成片）

1. **锁角色设定图**：若无现成角色图，先用 `h3studio.py run`（`type: "image"`）生成角色/场景设定图并请用户确认。
2. **排时间线与渲染合片**：运行 `h3studio.py director-compile script.json` 生成可离线微调的 `timeline.html`；确认镜头秒数与分辨率后运行 `h3studio.py director-render script.json --gpu L4 --confirmed --allow-unverified --output final.mp4` 远端渲染并直出成片（相邻镜头连拍可在剧本中设 `continue_previous: true`）。

### 工作流 3：⚡ 单件素材生成（单张图 / 单段视频 / 单条配音）

统一编写 `manifest.json`（`{"jobs": [...]}`），执行 `python scripts/h3studio.py run manifest.json --gpu L4 --allow-unverified`：

| 目标产物 | 任务配置（`type` 与模式） | 默认锁定配置（`profile`） |
|---|---|---|
| **静态图片** | `type: "image"`：文生图或最多 8 张 `@图片N` 参考精修（`align_to_first: true` 可锁死 `@图片1` 背景） | `ph3-lora-int8`（可选轻量 `ph3-standalone-w4a8`） |
| **无参考视频** | `type: "video"`：纯文字（`mode: "text"`）或**单/双张图钉死为首帧/尾帧**（`mode: "frames"`，填 `first`/`last`） | `h3-fl2va-int8` |
| **有素材参考视频** | `type: "video"`：自由参考角色/场景/视频/音色（`mode: "ref"`，引用 `@图片1` `@视频1` `@音频1`）；指定秒数卡画面（`mode: "guides"`）；边缘等控制视频（`mode: "control"`）；单段往后续拍（填 `extend`，含上下文 ≤15s） | `h3-ref2va-int8` / `h3-guides` / `h3-control-union`<br>*(加速可选 `fasth3-omni-int8` / `prism-dmad-pdmd-int8`)* |
| **单段关键帧传播** | `type: "video_edit"`：已有切好的单段视频 + 1–16 张精修锚点图 `anchors:[{"frame":0,"image":"..."}]` | `h3-ripple`（6 步 LCM） |
| **生成旁白音频** | `type: "tts"`：文字 + 可选最多 3 段参考音色 `voices`，生成 **2–15 秒**音频 | `h3-tts32-native` |

### 工作流 4：📝 纯后期音字处理（免 GPU，CPU 秒级合成）

- **单任务清单（`h3studio.py run --backend local` 或 Colab CPU）**：
  - `type: "voice_replace"`：把已有音频文件或 `@tts_id` 贴入视频指定 `[start, end]` 时段（默认静音该段原混合音轨）。
  - `type: "subtitle_replace"`：静态区域擦除（`clear_regions`）、烧录精确字幕（`subtitles`）或固定坐标叠字（`overlays`）。
- **整片批量改文案**：`request.json` 仅填 `copy`（字幕/叠字）不填人物商品改动时，`remake-render` 自动免 GPU 完成多版本渲染。

---

## 会话管理与执行铁律

1. **会话命令**：`h3studio.py check`（查登录与剩余点数）、`sessions`（查运行中实例）、`keep --session NAME --minutes 60`（延时保留）、`stop --session NAME` 或 `stop --all`（立即关机省点）。
2. **试跑与确认门禁**：未实测验证的配置首次试跑传 `480P` / `5 秒` 及 `--allow-unverified`；**2 个及以上视频片段必须先与用户确认规格再带 `--confirmed`**；任务跑完询问用户是否保留热机或立即 `stop`。
3. **断点恢复**：保留 `results.json`、seed、`prompt_id` 与 SHA256；断线重连加 `--resume`；局部重做用 `--only ID`（连带下游加 `--with-dependents`）；状态 `uncertain` 先查队列，严禁盲目重投。
4. 详细字段规范见 `references/简体中文操作指南.md`、`references/任务清单格式.md`、`references/提示词指南.md`。
