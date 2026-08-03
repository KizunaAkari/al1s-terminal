# AL-1S Terminal Agent

创龙 RK3576 ARM64/NPU 终端 Agent。Agent 负责 MaaFramework Pipeline、ADB 手机控制、远程控制中继和 RKNN NPU 自定义识别；PC 平台和前端不部署在终端上。

## NPU 容器部署

当前镜像专用于带 NPU 的创龙 RK3576 终端：

```bash
cp deploy/terminal/env.agent.npu.example deploy/terminal/env.agent.npu
vi deploy/terminal/env.agent.npu

docker compose -f deploy/terminal/docker-compose.npu.yml build
docker compose -f deploy/terminal/docker-compose.npu.yml up -d
docker compose -f deploy/terminal/docker-compose.npu.yml ps
```

终端需要安装 Docker，并通过 USB 连接已开启 ADB 调试的 Android 手机。模型目录通过 `MAA_MODEL_DIR` 挂载；RKNN 驱动仍由创龙主机内核提供。

## 主机侧部署器

平台一键部署功能会在终端主机上安装 `maa-terminal-deployer`，终端部署器只负责接收带令牌保护的镜像更新、校验 SHA-256、重建 Compose 容器和失败回滚。

更多说明见 [`deploy/terminal/CONTAINER-NPU.md`](deploy/terminal/CONTAINER-NPU.md)。

## 兼容的 systemd 部署

容器验证失败时，仍可以使用 `deploy/terminal/install.sh` 和 `maa-terminal-agent.service` 部署旧版主机 Agent。
