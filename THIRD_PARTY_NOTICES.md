# 上游来源与许可

本项目聚焦纯血 MiniMax H3 与 WepeNerd H3 Ripple 多模态工作流；未重新授予上游代码或权重许可。

| 内容 | 来源 | 固定版本 |
|---|---|---|
| ComfyUI 原生节点接口 | https://github.com/Comfy-Org/ComfyUI | `dependencies.lock.json` |
| 官方工作流结构 | https://github.com/Comfy-Org/workflow_templates | 同上 |
| WepeNerd H3 Ripple 与图像对齐合成节点 | https://github.com/WepeNerd/ComfyUI-WepeNerd | `a333adf90a764eb0852a34fd31c5b13e68346860`，MIT |
| MiniMax H3 / P-H3-Image / H3 Ripple 权重 | Hugging Face 各发布者 | `models/catalog.json` 逐文件锁定 revision、SHA256、字节数与许可链接 |

第三方节点与模型权重不包含在技能包内，由运行时按锁定 commit 与 SHA256 下载校验。
