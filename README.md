# H3 Studio Colab · 纯血 MiniMax H3 + WepeNerd H3 Ripple

专注 **MiniMax H3 原生多模态生成** 与 **WepeNerd H3 Ripple 关键帧视频复刻**，通过 Google Colab CLI 调度云端 GPU（或指定 `--backend local` 连接本地 ComfyUI）。所有命令统一收口于单一脚本 `scripts/h3studio.py`，所有模型仓库 revision、文件大小、SHA256 与自定义节点 commit 均严格锁定校验。

## 0. 安装为 AI Agent 技能（Antigravity / Claude Code / Codex / Cursor）

克隆到项目级 `.agents/skills/`（或全局 `~/.agents/skills/`）即可在聊天中通过 `/h3-studio-colab` 直接唤醒：

```bash
git clone https://github.com/Kiteflyingee/h3-studio-colab.git .agents/skills/h3-studio-colab
```

## 1. 环境检查与会话管理

本机需 Python 3.10+ 与已登录的 `colab` CLI（`uv tool install google-colab-cli && colab sessions`）：

```bash
python scripts/h3studio.py check
python scripts/h3studio.py sessions
python scripts/h3studio.py plan examples/basic-v3.json
python scripts/h3studio.py doctor --manifest examples/basic-v3.json --offline
python scripts/h3studio.py stop --all
```

GPU 会话默认在批次结束后空闲保留 15 分钟以便复用已加载的大模型（用 `--session NAME` 复用，`--stop-after` 可跑完立即关机）。当指定 `--gpu A100` 时，视频 VAE 自动切换为 `fp16` 以规避 `A100` 上的 int8 解码异常；`G4` / `A100` / `H100` 未填分辨率时默认采用 `720P`（`L4` 默认 `480P`）。

---

## 2. 四大标准工作流

### 工作流 1：爆款视频复刻（两步确认黄金流程）

复制 `examples/remake-v3.json`，填写源视频与要替换的人物/商品参考图：

```bash
# Step 1：切段并生成改后关键帧预览图与「原帧 | 改后帧」对比图（几十秒出图，先确认形象！）
python scripts/h3studio.py remake-anchors examples/remake-v3.json --gpu L4 --allow-unverified

# Step 2：确认预览图无误后，复用同一热机会话 6 步扩散全片并合成字幕/配音
python scripts/h3studio.py remake-render examples/remake-v3.json --session <SESSION> --confirmed --allow-unverified --output final.mp4
```

- **同镜改版（`"mode": "edit"`，默认）**：Step 1 使用 `P-H3-Image` 修改每段锚点帧并通过 `QwenEditAlignComposite` 仿射对齐、逐像素还原未改动背景；若某张锚点不理想，可加 `--only remake_001_anchor1` 秒级单张重抽或手工修图覆盖 `anchors/*.png`；Step 2 由 `WepeNerd H3 Ripple` 6 步 LCM 扩散至全段并保留原片音轨。
- **跨画风/跨物种重制（`"mode": "recreate"`）**：适合将真人爆款重制为 2D 动画/拟人猫咪 IP，自动将快切短镜合并至 `6–9.4s` 并生成 `640p` 静音参考代理提速 4 倍，直接运行 `remake-render` 即可。
- **仅改字幕/叠字**：省略人物商品改动时自动免 GPU 在 CPU 完成。

### 工作流 2：原创多镜头导演

```bash
python scripts/h3studio.py director-compile examples/director_example.json
python scripts/h3studio.py director-render examples/director_example.json --gpu L4 --confirmed --allow-unverified --output final.mp4
```

### 工作流 3 & 4：单件素材生成与后期音字合成

```bash
python scripts/h3studio.py run examples/basic-v3.json --gpu L4 --allow-unverified
python scripts/h3studio.py run examples/advanced-v3.json --gpu L4 --confirmed --allow-unverified --only animate --with-dependents
```

支持 `image`（生图/改图）、`video`（`text` / `frames` / `ref` / `guides` / `control` / `extend`）、`video_edit`（传入 `anchors` 跑 H3 Ripple 扩散）、`tts`（2–15 秒配音生成）、`voice_replace`（时段混音）与 `subtitle_replace`（字幕/静态擦除/固定叠字；macOS 标准版 FFmpeg 缺少 `libass`/`drawtext` 时自动回退透明 PNG `overlay` 合成）。

---

## 3. 离线契约与单元测试验证

```bash
python scripts/verify_release.py
```

自动验收覆盖全部单元测试、API 图导出、字节码编译与离线规划诊断。离线测试通过不代表真实 GPU 生成画质已验收，各配置状态详见 `acceptance/功能验收.json`，上游来源见 `THIRD_PARTY_NOTICES.md`。
