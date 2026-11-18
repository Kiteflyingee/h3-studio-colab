# H3 Studio Colab v4.0.0 发布与验收摘要

## 核心能力范围

| 模块 | 实现内容 |
|---|---|
| **工作流 1：爆款视频复刻** | 两步确认黄金流程：Step 1 (`remake-anchors`) 自动切段、抽取首/尾锚点帧，通过 `P-H3-Image` + `QwenEditAlignComposite` 仿射对齐还原原背景并输出 `原帧 \| 改后帧` 对比图；Step 2 (`remake-render`) 复用同一 `FL2VA` 底模，经 `WepeNerd H3 Ripple` 6 步 LCM 扩散全片；另支持 `recreate` 跨画风重制（快切自动合段 + 640p 静音参考代理提速 4 倍） |
| **工作流 2：原创多镜头导演** | 离线可视化时间线导出（`director-compile`）、分镜续接与远端一键渲染合片（`director-render`） |
| **工作流 3：单件素材生成** | 纯血 MiniMax H3 生图/改图（`ph3-lora-int8` / `ph3-standalone-w4a8`）、文生/首尾帧视频（`h3-fl2va-int8`）、多模态参考/时间点引导/控制视频/续接（`h3-ref2va-int8` / `h3-guides` / `h3-control-union`）、配音克隆（`h3-tts32-native`） |
| **工作流 4：免 GPU 后期合成** | 静态区域擦除、精确字幕烧录、固定坐标叠字（macOS 缺 `libass`/`drawtext` 时自动回退透明 PNG `overlay`）与时段混音 |
| **Colab 会话与安全工程** | 单一命令行 `scripts/h3studio.py`、全部 54 项权重锁定 revision 与 SHA256、`A100` 自动切换 `fp16` 视频 VAE、幂等 `prompt_id` 断点恢复与空闲自动关机看门狗 |

## 验证证据

运行 `python scripts/verify_release.py` 可复现全部 88 项单元与契约测试、API 图导出、字节码编译与离线诊断（记录于 `acceptance/verification.json` 与 `acceptance/unit-tests.txt`）。逐项能力与 GPU 实测状态详见 `acceptance/功能验收.json`。
