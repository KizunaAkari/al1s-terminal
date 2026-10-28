package com.al1s.terminal.setup

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

@SuppressLint("SetTextI18n")
class SetupActivity : Activity() {
    private lateinit var result: TextView
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 48, 32, 32) }
        layout.addView(TextView(this).apply { text = "首次设置与手动检查"; textSize = 24f })
        layout.addView(TextView(this).apply {
            text = "依次完成平台注册、本机配对与激活，并按系统设置允许通知、解除电池优化。无人值守运行需要由你关闭PIN/密码/图案等安全锁屏。厂商的休眠应用、后台和自启动限制需要在对应系统页面人工核对；无法读取的项目显示无法核实。"
            setPadding(0, 24, 0, 24)
        })
        layout.addView(button("本机配对与激活") { startActivity(Intent(this, com.al1s.terminal.activation.ActivationActivity::class.java)) })
        layout.addView(button("打开电池优化设置") { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) })
        layout.addView(button("打开锁屏与安全设置") { startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) })
        layout.addView(button("打开本APP系统设置") { startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            android.net.Uri.parse("package:$packageName"))) })
        layout.addView(button("执行完整检查") { inspect() })
        result = TextView(this).apply { setPadding(0, 24, 0, 0) }
        layout.addView(result)
        setContentView(ScrollView(this).apply { addView(layout) })
    }
    private fun button(label: String, action: () -> Unit) = Button(this).apply { text = label; setOnClickListener { action() } }
    private fun inspect() {
        val observation = runCatching { SetupInspector.inspect(this) }.getOrElse {
            result.text = "检查未完成：${it.javaClass.simpleName}"
            return
        }
        val labels = mapOf("platform_identity" to "平台注册", "notifications" to "通知权限", "local_pairing" to "本机配对",
            "control_helper" to "辅助服务", "secure_keyguard" to "无安全锁屏", "battery_exemption" to "电池优化豁免",
            "manufacturer_background_policy" to "厂商后台策略")
        val states = mapOf("ready" to "已就绪", "blocked" to "需要设置", "unverifiable" to "无法核实，请人工检查")
        val conditions = observation.getJSONObject("conditions")
        result.text = "实际检查时间：${observation.getString("checked_at")}\n\n" +
            labels.entries.joinToString("\n") { "${it.value}：${states[conditions.getString(it.key)]}" }
    }
}
