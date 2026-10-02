package com.al1s.terminal

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.al1s.terminal.runtime.RegistrationService
import com.al1s.terminal.runtime.SyncWorker
import com.al1s.terminal.runtime.TerminalForegroundService
import java.util.concurrent.Executors

@SuppressLint("SetTextI18n")
class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var platformUrl: EditText
    private lateinit var displayName: EditText
    private lateinit var registrationCode: EditText
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildContent())
        requestNotificationPermission()
        refreshStatus()
    }

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun buildContent(): ScrollView {
        val application = application as TerminalApplication
        val configuration = application.identityStore.configuration()
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
        }
        container.addView(TextView(this).apply {
            text = "AL-1S 小米 Root 终端 Demo"
            textSize = 24f
        })
        container.addView(TextView(this).apply {
            text = "只执行内置 root_probe；先安装平台公开 CA，再填写证书匹配的 HTTPS 地址。"
            textSize = 14f
            setPadding(0, 12, 0, 24)
        })
        platformUrl = input("平台地址，例如 https://al1s.local:8443", configuration?.first)
        displayName = input("终端名称", configuration?.second ?: "Xiaomi Root Demo")
        registrationCode = input("一次性注册码", null)
        container.addView(platformUrl)
        container.addView(displayName)
        container.addView(registrationCode)
        container.addView(button("注册或恢复终端") { register() })
        container.addView(button("启动前台任务服务") { startTerminalService() })
        container.addView(button("立即同步一次") { syncOnce() })
        status = TextView(this).apply {
            textSize = 15f
            setPadding(0, 24, 0, 0)
        }
        container.addView(status)
        return ScrollView(this).apply { addView(container) }
    }

    private fun input(hintText: String, initial: String?): EditText = EditText(this).apply {
        hint = hintText
        setText(initial.orEmpty())
        layoutParams = ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        )
    }

    private fun button(label: String, action: () -> Unit): Button = Button(this).apply {
        text = label
        setOnClickListener { action() }
    }

    private fun register() {
        status.text = "正在注册…"
        val baseUrl = platformUrl.text.toString()
        val name = displayName.text.toString()
        val code = registrationCode.text.toString()
        executor.execute {
            val result = runCatching {
                RegistrationService((application as TerminalApplication).identityStore)
                    .register(baseUrl, code, name)
            }
            runOnUiThread {
                status.text = result.fold(
                    onSuccess = { deviceId -> "注册成功\n逻辑手机：$deviceId" },
                    onFailure = { error -> "注册失败：${error.message}" },
                )
                if (result.isSuccess) {
                    registrationCode.setText("")
                    SyncWorker.schedule(this)
                    startTerminalService()
                }
            }
        }
    }

    private fun startTerminalService() {
        startForegroundService(Intent(this, TerminalForegroundService::class.java))
        SyncWorker.schedule(this)
        status.text = "前台任务服务已启动"
    }

    private fun syncOnce() {
        status.text = "正在同步…"
        executor.execute {
            val result = runCatching { (application as TerminalApplication).syncEngine().runOnce() }
            runOnUiThread {
                status.text = result.fold(
                    onSuccess = {
                        "同步完成：online=${it.online}，命令=${it.commandsSeen}，" +
                            "回传=${it.reportsSent}，执行=${it.execution}"
                    },
                    onFailure = { error -> "同步失败：${error.message}" },
                )
            }
        }
    }

    private fun refreshStatus() {
        val identity = (application as TerminalApplication).identityStore.load()
        status.text = if (identity == null) {
            "尚未注册"
        } else {
            "已注册\n终端：${identity.terminalId}\n逻辑手机：${identity.targetDeviceId}"
        }
    }

    private fun requestNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }
    }
}
