# AL-1S Android Terminal

Android 终端独立工程，当前只提供已 root 小米手机的安全 Demo；公开构建入口见[终端 README](../README.md)：

- 使用一次性注册码注册到 AL-1S 平台；
- 使用随机安装身份，不依赖 Android ID、ADB serial 或 IP；
- Room 持久保存任务 inbox/outbox；
- WorkManager 与前台服务复用平台 protocol v1；
- 仅执行 APK 内编译注册的 `root_probe`，不接受任意 Shell。

注册前，在 Android 系统的证书设置中安装当前平台的公开 `ca.crt`（见部署台账的证书目录），再填写与平台证书 SAN 匹配的 HTTPS 地址。APK 信任系统和用户安装的 CA，仍验证证书链与主机名；不安装 CA 私钥或服务器私钥。旧 HTTP 配置不会继续发送注册信息或长期凭据，需改填 HTTPS 后重新注册。证书或主机名不匹配时应修正地址/证书，不绕过校验。

非 root、厂商保活、MaaFramework Android Native、OCR/YOLO 和业务脚本执行不属于此 Demo。

## 构建

要求 JDK 17+、Android SDK 36 与 Gradle 8.13。本仓库没有 Gradle Wrapper，安装对应 Gradle 或在 Android Studio 中配置该版本后，从本目录执行：

```sh
gradle :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

Gradle中间输出仍在 `app/build/outputs/`；对外交付APK统一收集到 `al1s-deployment/packages/android/`。已移动的调试包不代表生产签名发布。
