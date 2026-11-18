# 工程审查与质量回归记录

| 审查项 | 处理措施 | 对应回归测试 |
|---|---|---|
| 断线或下载中断导致重复提交 | 已提交 prompt 保留 `running`/`collecting` 状态与缓存 history；仅确认推理终止失败才允许重投；输出目录加文件锁 | `test_review_regressions.ReviewRecoveryTests` |
| Colab 会话误关或无法重连 | 批次身份落盘 `remote-session.json`，同批次幂等重连；未完成收集绝不释放实例；空闲看门狗检查远端队列 | `test_review_remote.py` |
| 未确认关键帧误跑长视频浪费算力 | `remake-render` 在 `edit` 模式下强制校验 Step 1 `remake-anchors` 已确认的关键帧图片 | `test_ripple_adapter.TwoStepRippleRemakeTests` |
| 非 `17k+5` 帧数镜头被 Ripple 截断 | 图内自动使用 `ImageFromBatch + RepeatImageBatch + ImageBatch` 补齐尾帧并在收集时精确裁回源时长 | `test_video_edit.py`、`test_ripple_adapter.py` |
| `A100` 视频 VAE int8 解码崩溃 | 打通锁定 `vae__minimax_h3_video_vae_fp16` 并在 `--gpu A100` 及 `h3-ripple` 下自动启用 `fp16` VAE | `test_macos_and_a100_fixes.py` |
| macOS 软链路径拦截与标准版 FFmpeg 缺滤镜 | `artifact_target` 兼容符号链接归一化；缺少 `libass`/`drawtext` 时自动回退透明 PNG `overlay` 合成并透传 FFmpeg `stderr` | `test_macos_and_a100_fixes.py`、`test_copy_replace.py` |
| 源码归档意外上传用户素材或密钥 | 严格按目录与扩展名白名单打包应用源码，仅上传当前批次声明的输入素材 | `test_source_archive_excludes_user_media_and_secrets` |
