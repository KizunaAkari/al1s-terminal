# NPU 终端容器试运行

这是一套面向 RK3576 等 ARM64、带 NPU 开发板的终端镜像。镜像标签使用
`npu-arm64`，包含 Agent、Python 3.12、MaaFramework、RKNN Lite、RKNN 用户态运行库和 OCR 资源。

容器不包含手机，也不会替代开发板内核里的 RKNPU 驱动。开发板宿主机需要提供 Docker，
并通过 USB 连接已开启 ADB 调试的 Android 手机。首版 Compose 使用 `privileged`，用于同时验证
USB ADB 和 NPU 设备访问；验证稳定后再收紧到具体设备节点。

## 准备

在终端上准备 Docker，并确认手机已连接：

```bash
adb devices
```

如果终端尚未安装 ADB，可安装系统的 `android-tools-adb`。镜像内也包含 ADB 客户端，
用于在容器内执行现有 Agent 的 ADB 命令。

把 `env.agent.npu.example` 复制为 `env.agent.npu`，填写平台地址、Agent Token、终端 IP 和 Agent ID：

```bash
cp env.agent.npu.example env.agent.npu
vi env.agent.npu
```

默认模型目录是当前开发板的：

```text
/run/media/mmcblk1p8/workspace/yolov8
```

如果模型放在其他位置，在启动 Compose 前设置：

```bash
export MAA_MODEL_DIR=/path/to/yolov8
```

目录中应包含 `yolov8n_640x640_rk3576.rknn`。也可以修改 Compose 中的模型目录映射。

## 构建和启动

在项目根目录执行：

```bash
docker compose -f deploy/terminal/docker-compose.npu.yml build
docker compose -f deploy/terminal/docker-compose.npu.yml up -d
docker compose -f deploy/terminal/docker-compose.npu.yml ps
docker compose -f deploy/terminal/docker-compose.npu.yml logs -f terminal-agent
```

首次构建会下载 ARM64 的 MaaFramework、RKNN Lite、NPU 用户态运行库和 OCR 模型，
因此建议在网络正常的环境中构建并缓存镜像。构建成功后，镜像名为：

```text
al1s-terminal-agent:npu-arm64
```

检查 Agent：

```bash
curl http://127.0.0.1:8765/health
docker exec -it terminal-agent adb devices
```

Compose 使用 host 网络模式，平台需要能访问终端的 `8765`，浏览器实时控制还需要访问终端的
`8766`。ADB 的 `5037` 仍由 Agent 访问，不配置为对外服务端口。

host 网络模式是为了兼容部分精简开发板内核：这类内核可能没有 Docker bridge 所需的
iptables `raw` 表。终端 Agent 是单实例服务，使用 host 网络不会影响当前功能。

## 停止和回退

```bash
docker compose -f deploy/terminal/docker-compose.npu.yml down
```

现有的 `deploy/terminal/install.sh` 和 `maa-terminal-agent.service` 仍然保留，
容器验证失败时可以继续使用原来的 systemd 部署方式。容器运行数据位于
`TERMINAL_DATA_DIR`，ADB 授权信息位于 `ADB_HOME_DIR`，删除容器不会自动删除这两个目录。

## 当前试运行限制

- 首版使用 `privileged: true`，只用于验证 USB 和 NPU 通路，不建议直接作为最终安全配置。
- RKNN 用户态运行库放在镜像内，宿主机仍必须有匹配的 RK3576 BSP 内核驱动。
- 这是 ARM64/NPU 专用镜像，不应部署到普通 x86 Docker 主机。
- 后续验证通过后，可以改成 GitHub Actions 构建并推送
  `ghcr.io/<owner>/al1s-terminal-agent:npu-arm64`，终端只负责拉取指定版本。

## 平台一键部署（Beta）

当前只开放给带 NPU 的创龙 RK3576 ARM64 终端。首次在开发板上安装一次主机侧部署器，之后就可以从平台终端卡片点击“部署 NPU 容器”：

```powershell
.\scripts\install-terminal-deployer.ps1 -BoardHost 192.168.5.21
```

安装脚本会上传 `terminal-deployer.py`、systemd 服务和当前 Compose 文件，但不会替用户写入平台令牌。首次安装后在开发板编辑：

```bash
vi /run/media/mmcblk1p8/maa-test-terminal/deploy/terminal/env.deployer
```

把 `DEPLOYER_TOKEN` 设置为平台 `.env` 中的 `AGENT_TOKEN`，然后启动：

```bash
systemctl restart maa-terminal-deployer
systemctl status maa-terminal-deployer
curl http://127.0.0.1:8767/health
```

平台 `.env` 还需要设置一个“终端能访问的平台地址”，例如：

```dotenv
PUBLIC_BASE_URL=http://192.168.5.3:8000
TERMINAL_DEPLOY_PUBLIC_URL=http://192.168.5.3:8000
```

在平台部署弹窗中上传已经构建好的 `al1s-terminal-agent:npu-arm64.tar`。镜像会保存到平台 Docker 数据卷的
`/app/data/terminal-deploy`，终端部署时通过局域网下载并校验 SHA-256，不再直接访问 Docker Hub、GitHub 或 PyPI。
平台现有的 `export` 导出包会连同这个缓存一起带走。

部署器会先保存旧镜像，导入新镜像后执行 `docker compose up -d --no-build`，并等待容器健康检查；启动失败时自动回滚旧镜像。
Agent 容器不挂载 Docker Socket，主机部署器单独受令牌保护。部署前应确保没有正在执行的任务；部署器会停止旧的
`maa-terminal-agent` systemd 服务，容器部署成功后由 Compose 负责保持运行。
