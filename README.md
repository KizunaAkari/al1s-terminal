# AL-1S Terminals

本仓库包含 [Linux 终端](al1s-terminal-linux/README.md)和 [Android 终端](al1s-terminal-android/README.md)。Linux 执行 Maa/ADB/RKNN 任务并持久保存补报事实；Android 当前提供已 root 手机的安全 Demo。两端使用[平台](https://github.com/KizunaAkari/al1s-platform)发放的一次性注册码和独立终端凭据。

```sh
git clone https://github.com/KizunaAkari/al1s-terminal.git
cd al1s-terminal
```

需要统一部署文件时，使用 `git clone --recurse-submodules https://github.com/KizunaAkari/al1s-platform.git AL1S`，本仓库会位于 `AL1S/terminals/`。

## Linux 终端容器

完整运行镜像面向 Linux ARM64 / RK3576。需要 Docker Buildx 及 ARM64 原生构建器或模拟支持；镜像包含 Maa、OCR 和 RKNN 依赖。

**源码仓库不包含 `.terminal-assets/` 离线资源包。** 先准备与 `al1s-terminal-linux/tools/assets-arm64.sha256` 匹配的完整资源：ARM64 Python 3.12 wheels、OCR 模型、RKNN runtime、nss-mdns 库、scrcpy server 和 `lineup-v1` 资料/模型。准备脚本可下载 wheels 并复制基础 seed 文件，但不会自动获取完整 scrcpy 和阵容资源；只有所有清单项都齐全才可构建，不删除清单项绕过校验。

准备完整资源后，PowerShell 校验并构建：

```powershell
cd al1s-terminal-linux
./scripts/Prepare-Arm64Assets.ps1 -VerifyOnly
docker buildx build --platform linux/arm64 --load -f Dockerfile.arm64 -t al1s-terminal:local .
```

Linux ARM64 或已配置 ARM64 模拟的构建机：

```sh
cd al1s-terminal-linux
(cd .terminal-assets && sha256sum --check ../tools/assets-arm64.sha256)
docker buildx build --platform linux/arm64 --load -f Dockerfile.arm64 -t al1s-terminal:local .
```

有基础 seed 资源时可运行 `./scripts/Prepare-Arm64Assets.ps1 -SeedAssetDirectory /path/to/seed-assets` 恢复 wheels、基础 OCR、RKNN runtime 和 nss-mdns，再补齐清单中的其他资源。阵容资料与模型生成工具见 Linux 子项目 README。

构建不注册终端，也不提供宿主 NPU 驱动、USB/ADB 授权或运行模型挂载。统一工作区中使用 `al1s-deployment/linux/compose.arm64.yaml`；`AL1S_TERMINAL_IMAGE` 设置为自己的已构建镜像（如 `al1s-terminal:local`），并配置持久身份、ADB、模型、证书目录及日志收集器。长期凭据只连接经过证书验证的 HTTPS 平台。保持容器名 `al1s-terminal-linux`。

## Android APK

Android 终端不构建 Docker 镜像。需要 JDK 17+、Android SDK 36 和 Gradle 8.13；本仓库没有 Gradle Wrapper，可使用本机 Gradle 或 Android Studio 配置对应版本。

```sh
cd al1s-terminal-android
gradle :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

APK 输出到 `app/build/outputs/apk/debug/`。真机注册前安装平台公开 CA，使用证书主机名匹配的 HTTPS 地址；不要复制 CA 私钥或平台服务器私钥。详细边界见 Android 子项目 README。

各子项目和仓库根目录的 `.gitignore` 排除终端身份、凭据、签名配置、数据库、依赖、构建产物和离线资源；示例和公开配置保留。
