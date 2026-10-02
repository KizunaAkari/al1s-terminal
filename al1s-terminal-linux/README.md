# AL-1S Linux Terminal

Linux 终端负责身份、持久 inbox/outbox、任务包缓存、Maa/ADB/RKNN Provider、媒体与交互通道。旧终端仅作行为参考，不沿用旧长轮询或内存队列架构。

统一克隆与构建入口见[终端 README](../README.md)，平台部署文件在[平台仓库](https://github.com/KizunaAkari/al1s-platform)。本机实现记录与运行台账另行维护，不随源码发布。

## 本地检查

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

硬件 Provider 检查需要相应运行环境；模拟通过不代表手机或 NPU 验收。依赖按项目锁文件维护。

## 阵容识别资源

`al1s_terminal/lineup` 执行本机图片任务，资源位于 `/opt/al1s-assets/lineup-v1`，缺少资源不声明识别能力。标准 ARM64 构建从本项目 `.terminal-assets/lineup-v1` 打包，`tools/assets-arm64.sha256` 校验模型、资料和头像；平台内的 `al1s/lineup/assets` 必须使用同一资料版本。

开发工具在 `tools/lineup`：准备指定学生 ID 与头像资料、生成合成布局并训练定位模型、导出 ONNX。训练依赖属于开发环境，不加入终端运行依赖；用户实图保留为独立验收样图。运行版本及模型摘要见部署台账，训练和实测结果见测试计划。

完成基础资源后，运行 `python tools/lineup/prepare-recognition-v2.py .terminal-assets/lineup-v1` 安装固定提交、SHA256 校验的阵容专用 OCR 和剑盾模板。它使用 `lineup-v1/ocr`，手机脚本仍使用原有独立 OCR 路径。模板来源随 `tools/lineup/roles/README.md` 记录。

专用 NPU 检测器由同一 `detector.onnx` 转换：在 Linux x86_64 / Python 3.12 开发环境安装 `tools/lineup/requirements-rknn.txt`（系统需 libGL、GLib、OpenMP），运行 `python tools/lineup/convert-lineup-rknn.py .terminal-assets/lineup-v1/detector.onnx .terminal-assets/lineup-v1/detector.rknn`。转换为 RK3576 FP16 三尺度六检测头，DFL/锚点解码在 CPU 使用 FP32；保留 RGB、114 补边及 /255 归一化；输出模型与摘要侧文件均需加入资产校验清单。存在 RKNN 模型时使用真实 NPU，初始化失败报错；没有该模型时保留 ONNX CPU 兼容路径。OCR 和头像模板比对仍在 CPU 执行，部署与验收结论见对应文档。

## 交互通道

scrcpy relay 使用独立 video/control WebSocket URL；正式任务或临时测试取得输入权时关闭人工 control 并拒绝新控制连接，保留 video。配置 AL1S_TERMINAL_SCRCPY_PUBLIC_URL 使用可解析主机名，不保存 DHCP 地址。浏览器可经平台同源转发，终端的令牌与控制权限校验仍保留。

## 宿主机部署器

独立 zipapp 部署器不进入 Agent 镜像，其源码保持 Python 3.10 标准库兼容。通过终端 wheel 和安装脚本安装的宿主管理服务则要求 Python 3.12，使用安装器创建的虚拟环境；环境与执行入口见[部署说明](https://github.com/KizunaAkari/al1s-platform/blob/main/al1s-deployment/README.md)。

```text
python -m terminal_deployer.build dist/al1s-terminal-deployer.pyz
```

受限部署器接收校验后的本地 manifest/镜像归档；远程升级由宿主管理鉴权下载后调用，不开放任意 Shell。校验 SHA-256、arm64、迁移、RKNN 与健康，失败保留当前数据库和待补传结果；只有确认 schema 兼容才回退程序，不自动覆回旧库。

## 完整容器构建

先按照[终端 README](../README.md#linux-终端容器)准备 `.terminal-assets/` 中的完整离线资源。Windows PowerShell 校验命令是 `./scripts/Prepare-Arm64Assets.ps1 -VerifyOnly`；Linux 下在本目录执行：

```sh
(cd .terminal-assets && sha256sum --check ../tools/assets-arm64.sha256)
docker buildx build --platform linux/arm64 --load -f Dockerfile.arm64 -t al1s-terminal:local .
```

`--load` 将单架构构建结果加载到本机 Docker 镜像库，见 [Docker Buildx 文档](https://docs.docker.com/reference/cli/docker/buildx/build/)。镜像构建不会注册终端、创建运行身份或替换已部署容器。宿主 NPU 驱动、运行模型和 USB/ADB 权限需在目标设备另行准备。
