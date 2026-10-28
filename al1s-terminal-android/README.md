# AL-1S Android Terminal

Android 终端独立工程，提供平台注册、身份与耐久通信，以及单APK本机无线配对、受限辅助服务和Native Maa接入；公开构建入口见[终端 README](../README.md)。功能目标、实现状态与验证分别维护在[需求](../../docs/product/product-requirements.md#android-direct)、[开发清单](../../docs/development/delivery-workflow.md#android-direct)及[测试计划](../../docs/testing/test-plan.md#android-direct)：

- 使用一次性注册码注册到 AL-1S 平台；
- 使用随机安装身份，不依赖 Android ID、ADB serial 或 IP；
- Room 持久保存任务 inbox/outbox；
- WorkManager 与前台服务复用平台 protocol v1；
- 本机配对使用通知内输入六位码，APP发现两类端口，配对码不外发；
- Native/原图/视频分别验证，控制操作经受限Binder，平台不下发任意Shell。

注册前，在 Android 系统的证书设置中安装当前平台的公开 `ca.crt`（见部署台账的证书目录），再填写与平台证书 SAN 匹配的 HTTPS 地址。APK 信任系统和用户安装的 CA，仍验证证书链与主机名；不安装 CA 私钥或服务器私钥。旧 HTTP 配置不会继续发送注册信息或长期凭据，需改填 HTTPS 后重新注册。证书或主机名不匹配时应修正地址/证书，不绕过校验。

首次设置从「本机配对与激活」进入。无线调试恢复默认关闭，仅在手机单独确认后请求可选权限；注册和本机控制分别显示结果。平台业务能力以实际Provider、资源与测试为准，不能用容器/连接在线代替。

## 构建

要求 JDK 17+、Android SDK 36、NDK 27.2.12479018、CMake 3.22.1与Gradle 8.13。本仓库没有 Gradle Wrapper，安装对应 Gradle 或在 Android Studio 中配置该版本后，从本目录执行：

```sh
gradle :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

Gradle中间输出仍在 `app/build/outputs/`；对外交付APK统一收集到 `al1s-deployment/packages/android/`。已移动的调试包不代表生产签名发布。
