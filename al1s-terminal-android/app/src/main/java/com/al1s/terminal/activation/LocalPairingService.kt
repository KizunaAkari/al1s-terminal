package com.al1s.terminal.activation

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.RemoteInput
import android.app.Service
import android.content.Intent
import android.net.Uri
import android.os.IBinder
import android.os.SystemClock
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** User initiated local activation, bounded to one notification session; never contacts the platform. */
class LocalPairingService : Service() {
    private val executor = Executors.newSingleThreadExecutor()
    private val watchdog = Executors.newSingleThreadScheduledExecutor()
    private val gate = PairingRequestGate(UUID.randomUUID().toString(), now())
    private var discovery: PairingDiscovery? = null
    @Volatile private var port = -1
    private var started = false
    private val finished = AtomicBoolean(false)
    private val manager by lazy { getSystemService(NotificationManager::class.java) }

    override fun onCreate() {
        super.onCreate()
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "本机配对", NotificationManager.IMPORTANCE_HIGH))
    }

    private fun beginSession() {
        started = true
        startForeground(ID, notification("在系统无线调试中打开“使用配对码配对设备”", false))
        discovery = PairingDiscovery(this, true) { found ->
            if (gate.awaitingCode(now())) {
                port = found
                manager.notify(ID, notification("已发现本机配对窗口；在此通知输入六位码", true))
            }
        }.also { it.start() }
        watchdog.schedule({ finish("配对已超时，请重新开始") }, 180, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == START && !started) beginSession()
        if (!started) { stopSelf(startId); return START_NOT_STICKY }
        if (intent?.action == CANCEL && intent.getStringExtra("session") == gate.sessionId) {
            gate.cancel(); finish("已取消本机配对")
        } else if (intent?.action == PAIR) {
            val code = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(CODE)?.toString().orEmpty()
            intent.clipData = null
            if (port > 0 && gate.accept(intent.getStringExtra("session"), code, now())) {
                discovery?.close()
                manager.notify(ID, notification("正在配对并激活本机辅助服务", false))
                executor.execute { pairAndActivate(port, code) }
            }
        }
        return START_NOT_STICKY
    }

    private fun pairAndActivate(pairingPort: Int, code: String) {
        val activation = ActivationCoordinator(applicationContext)
        var paired = false
        val result = runCatching {
            check(activation.pair(pairingPort, code)); paired = true
            check(gate.isActive(now()))
            check(activation.activate(PairingDiscovery.connectionPort(this)) { gate.isActive(now()) })
            "本机配对与辅助服务激活完成；可返回APP验证Native"
        }
        if (gate.isActive(now())) finish(result.getOrElse {
            (if (paired) "配对已完成，辅助服务未就绪" else "配对未完成") + "：${it.javaClass.simpleName}，请重新核对"
        })
    }

    private fun notification(text: String, input: Boolean): Notification {
        val open = PendingIntent.getActivity(this, 1, Intent(this, ActivationActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val builder = Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("AL-1S 本机配对").setContentText(text).setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(open).setOngoing(input || text.startsWith("正在"))
        if (input) {
            val reply = sessionIntent(PAIR)
            val pending = PendingIntent.getService(this, 2, reply, PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            builder.addAction(Notification.Action.Builder(null, "输入配对码", pending)
                .addRemoteInput(RemoteInput.Builder(CODE).setLabel("六位配对码").build()).build())
        }
        if (gate.isActive(now())) builder.addAction(Notification.Action.Builder(null, "取消", PendingIntent.getService(this, 3, sessionIntent(CANCEL),
            PendingIntent.FLAG_IMMUTABLE)).build())
        return builder.build()
    }

    private fun finish(text: String) {
        if (!finished.compareAndSet(false, true)) return
        gate.cancel()
        getSharedPreferences("local-activation-status", MODE_PRIVATE).edit().putString("result", text).apply()
        stopForeground(STOP_FOREGROUND_REMOVE)
        manager.notify(ID + 1, notification(text, false))
        stopSelf()
    }

    private fun sessionIntent(action: String) = Intent(this, LocalPairingService::class.java).setAction(action)
        .setData(Uri.parse("al1s-local-pairing://${gate.sessionId}/$action")).putExtra("session", gate.sessionId)

    override fun onDestroy() {
        finished.set(true); gate.cancel(); discovery?.close(); executor.shutdownNow(); watchdog.shutdownNow(); super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL = "al1s-local-pairing"
        private const val ID = 8102
        private const val PAIR = "com.al1s.terminal.LOCAL_PAIR"
        private const val CANCEL = "com.al1s.terminal.LOCAL_PAIR_CANCEL"
        private const val CODE = "local_pairing_code"
        const val START = "com.al1s.terminal.LOCAL_PAIR_START"
        private fun now() = SystemClock.elapsedRealtime() / 1000
    }
}
