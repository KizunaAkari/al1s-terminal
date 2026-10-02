package com.al1s.terminal.runtime

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import com.al1s.terminal.MainActivity
import com.al1s.terminal.R
import com.al1s.terminal.TerminalApplication
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class TerminalForegroundService : Service() {
    private val executor = Executors.newSingleThreadScheduledExecutor()

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, notification("等待同步"))
        val application = application as TerminalApplication
        executor.scheduleWithFixedDelay(
            {
                try {
                    val result = application.syncEngine().runOnce()
                    val summary = if (result.online) {
                        "已连接 · 命令 ${result.commandsSeen} · ${result.execution}"
                    } else {
                        "离线 · ${result.execution}"
                    }
                    getSystemService(NotificationManager::class.java)
                        .notify(NOTIFICATION_ID, notification(summary))
                } catch (error: Exception) {
                    Log.w("AL1S-Sync", "cycle_failed:${error.javaClass.simpleName.take(40)}")
                    runCatching {
                        getSystemService(NotificationManager::class.java)
                            .notify(NOTIFICATION_ID, notification("同步失败，下次重试"))
                    }
                }
            },
            0,
            15,
            TimeUnit.SECONDS,
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.service_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun notification(summary: String): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle(getString(R.string.service_notification_title))
            .setContentText(summary)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "al1s-terminal-sync"
        private const val NOTIFICATION_ID = 8101
    }
}
