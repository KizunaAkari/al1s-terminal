package com.al1s.terminal.activation

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Point
import android.os.Build
import android.os.Bundle
import android.os.SharedMemory
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.al1s.terminal.broker.BrokerClient
import java.util.concurrent.Executors

@SuppressLint("SetTextI18n")
class ActivationActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    private val activation by lazy { ActivationCoordinator(applicationContext) }
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 48, 32, 32) }
        layout.addView(TextView(this).apply { text = "本机配对与激活"; textSize = 24f })
        layout.addView(TextView(this).apply {
            text = "点击引导配对，在系统无线调试中打开“使用配对码配对设备”。保持窗口打开，下拉通知，在AL-1S通知中输入六位码。APP自动发现端口，配对码不发送到平台。"
            setPadding(0, 24, 0, 24)
        })
        layout.addView(Button(this).apply { text = "开启引导配对"; setOnClickListener { startGuidedPairing() } })
        layout.addView(Button(this).apply { text = "使用已有配对自动激活"; setOnClickListener {
            runAction { check(activation.activate(PairingDiscovery.connectionPort(this@ActivationActivity))); "本机辅助服务已握手" }
        } })
        layout.addView(Button(this).apply { text = "验证 Native 与原图"; setOnClickListener {
            if (Build.VERSION.SDK_INT >= 27) runAction { verifyOriginalFrame() }
            else status.text = "原图辅助通道需要Android 8.1或更新版本"
        } })
        layout.addView(Button(this).apply { text = "验证实时视频"; setOnClickListener {
            runAction { verifyVideo() }
        } })
        layout.addView(Button(this).apply { text = "验证 Native 任务接口"; setOnClickListener {
            runAction {
                val result = BrokerClient.invoke("verify_task")
                check(result.getBoolean("success"))
                val events = org.json.JSONObject(result.getString("events").orEmpty()).getJSONArray("events")
                "Native Tasker 已完成无输入验证 · ${events.length()} 条实际事件"
            }
        } })
        layout.addView(Button(this).apply { text = "验证文字识别"; setOnClickListener {
            runAction {
                check(BrokerClient.invoke("verify_ocr").getBoolean("success"))
                "CPU OCR 已识别当前页面标题，Native任务已完成"
            }
        } })
        layout.addView(Button(this).apply {text="验证录屏文件";setOnClickListener {
            runAction {
                val result=BrokerClient.invoke("verify_recording")
                android.util.Log.i("AL1S-MediaProbe","artifact_present=${result.containsKey("artifact")}; bytes=${result.getLong("size_bytes")}")
                @Suppress("DEPRECATION") val descriptor=checkNotNull(result.getParcelable<android.os.ParcelFileDescriptor>("artifact"))
                val local=java.io.File.createTempFile("al1s-recording-probe-",".mp4",cacheDir)
                android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor).use {input->
                    local.outputStream().use {output->input.copyTo(output)}
                }
                android.util.Log.i("AL1S-MediaProbe","copied_bytes=${local.length()}; expected_bytes=${result.getLong("size_bytes")}")
                check(local.length()==result.getLong("size_bytes")) {"recording_transfer_incomplete"}
                val media=android.media.MediaMetadataRetriever()
                try {
                    media.setDataSource(local.absolutePath)
                    val duration=media.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0
                    check(duration>500) {"recording_duration_missing"}
                    "MP4 已封装并读取 · 时长 $duration 毫秒 · ${result.getLong("size_bytes")} 字节"
                } catch(error:Exception) {
                    android.util.Log.e("AL1S-MediaProbe","MP4 metadata verification failed",error)
                    throw error
                } finally {media.release();local.delete()}
            }
        }})
        layout.addView(Button(this).apply { text = "允许恢复无线调试（可选）"; setOnClickListener {
            android.app.AlertDialog.Builder(this@ActivationActivity)
                .setTitle("允许恢复无线调试？")
                .setMessage("首次本机配对和激活完成后，授予本APP WRITE_SECURE_SETTINGS。仅用于恢复无线调试开关；不会修改锁屏、开启明文ADB或接受平台任意Shell。默认关闭，可随时停用；系统撤销权限后不会自动重新授予。")
                .setNegativeButton("取消", null)
                .setPositiveButton("我确认允许") { _, _ -> runAction {
                    WirelessRecovery(applicationContext).enableFromExplicitConfirmation()
                    "已允许恢复无线调试；重启恢复能力仍需实际验证"
                } }.show()
        } })
        layout.addView(Button(this).apply { text = "停用无线调试恢复"; setOnClickListener {
            WirelessRecovery(applicationContext).disable()
            status.text = "已停用恢复，不修改当前无线调试开关"
        } })
        status = TextView(this).apply { setPadding(0, 24, 0, 0) }
        layout.addView(status)
        setContentView(ScrollView(this).apply { addView(layout) })
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) status.text = if (BrokerClient.connected()) "本机辅助服务已握手"
            else getSharedPreferences("local-activation-status", MODE_PRIVATE)
                .getString("result", "平台注册与本机控制激活分别检查")
    }

    private fun startGuidedPairing() {
        if (Build.VERSION.SDK_INT < 30) { status.text = "系统无线配对需要Android 11或更新版本"; return }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 101)
            status.text = "请允许本机配对通知，再点击引导配对"
            return
        }
        startForegroundService(Intent(this, LocalPairingService::class.java).setAction(LocalPairingService.START))
        startActivity(Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun runAction(action: () -> String) {
        status.text = "正在检查…"
        worker.execute {
            val result = runCatching(action)
            runOnUiThread { if (!isDestroyed) status.text = result.getOrElse {
                "未完成：${it.javaClass.simpleName}；本机服务与Native能力尚未确认"
            } }
        }
    }

    @androidx.annotation.RequiresApi(27)
    private fun verifyOriginalFrame(): String {
        val size = Point()
        @Suppress("DEPRECATION") windowManager.defaultDisplay.getRealSize(size)
        val result = BrokerClient.invoke("native_connect", Bundle().apply { putInt("width", size.x); putInt("height", size.y) })
        val screenshot = BrokerClient.invoke("screenshot")
        @Suppress("DEPRECATION") val memory = screenshot.getParcelable<SharedMemory>("image") ?: error("original_frame_absent")
        val image = try {
            val mapping = memory.mapReadOnly()
            try {
                check(mapping.remaining() == screenshot.getInt("length") && mapping.remaining() in 24..(32 * 1024 * 1024))
                ByteArray(mapping.remaining()).also { mapping.get(it) }
            } finally { SharedMemory.unmap(mapping) }
        } finally { memory.close() }
        check(image.take(8) == listOf(137, 80, 78, 71, 13, 10, 26, 10).map { it.toByte() }) { "original_frame_not_png" }
        val info = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(image, 0, image.size, info)
        check(info.outWidth == size.x && info.outHeight == size.y) { "original_geometry_mismatch" }
        return "Maa ${result.getString("maa_version")} · 原始PNG ${info.outWidth}×${info.outHeight} 已取回"
    }

    private fun verifyVideo(): String {
        val session = BrokerClient.invoke("media_open")
        val id = checkNotNull(session.getString("session_id"))
        @Suppress("DEPRECATION") val descriptor = session.getParcelable<android.os.ParcelFileDescriptor>("video")
        try {
            java.io.DataInputStream(android.os.ParcelFileDescriptor.AutoCloseInputStream(checkNotNull(descriptor))).use { input ->
                val codec = input.readInt()
                val width = input.readInt(); val height = input.readInt()
                require(codec == 0x68323634 && width in 1..1600 && height in 1..1600)
                repeat(16) {
                    val flags = input.readLong()
                    val length = input.readInt()
                    require(length in 1..(4 * 1024 * 1024))
                    val frame = ByteArray(length)
                    input.readFully(frame)
                    if (flags >= 0) return "scrcpy 3.3.4 · H.264 ${width}×${height} · 图像帧 $length 字节已收到"
                }
                error("video_picture_frame_absent")
            }
        } finally {
            runCatching { descriptor?.close() }
            BrokerClient.invoke("media_close", Bundle().apply { putString("session_id", id) })
        }
    }

    override fun onDestroy() { worker.shutdownNow(); super.onDestroy() }
}
