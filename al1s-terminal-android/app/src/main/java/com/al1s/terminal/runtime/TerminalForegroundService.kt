package com.al1s.terminal.runtime

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log
import com.al1s.terminal.MainActivity
import com.al1s.terminal.R
import com.al1s.terminal.TerminalApplication
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class TerminalForegroundService : Service() {
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private val reconciliation = Executors.newSingleThreadScheduledExecutor()
    private val controlRecovery = Executors.newSingleThreadScheduledExecutor()
    private val execution = Executors.newSingleThreadScheduledExecutor()
    private val editor = Executors.newSingleThreadScheduledExecutor()
    private val setupChecks = Executors.newSingleThreadScheduledExecutor()
    private val devicePaths = Executors.newSingleThreadScheduledExecutor()
    private val quickTests = Executors.newSingleThreadScheduledExecutor()
    private lateinit var editorCoordinator: com.al1s.terminal.editor.AndroidEditorCoordinator
    private val policy = AgentRetryPolicy()
    private val active = AtomicBoolean(true)
    private lateinit var reachability:PlatformReachability
    private val reachabilityHttp=okhttp3.OkHttpClient.Builder()
        .followRedirects(false).followSslRedirects(false)
        .connectTimeout(8,TimeUnit.SECONDS).writeTimeout(3,TimeUnit.SECONDS)
        .readTimeout(0,TimeUnit.SECONDS).build()
    private var networkCallback:ConnectivityManager.NetworkCallback?=null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, notification("等待同步"))
        if (!AgentCoordinator(this).mayRun()) { stopSelf(); return }
        startReachability()
        scheduleHeartbeat(0)
        val app = application as TerminalApplication
        val quick = QuickTestCoordinator(this,app.database,app.identityStore)
        quickTests.scheduleWithFixedDelay({
            if(active.get() && AgentCoordinator(this).mayRun())runCatching {quick.cycle()}
                .onFailure {Log.w("AL1S-Debug","reconcile_failed:${it.javaClass.simpleName}")}
        },0,1,TimeUnit.SECONDS)
        val setup = SetupCheckCoordinator(this,app.database,app.identityStore)
        setupChecks.scheduleWithFixedDelay({
            if(active.get() && AgentCoordinator(this).mayRun())runCatching {setup.cycle()}
                .onFailure {Log.w("AL1S-Setup","check_failed:${it.javaClass.simpleName}")}
        },0,30,TimeUnit.SECONDS)
        if (android.os.Build.VERSION.SDK_INT >= 27) {
            editorCoordinator = com.al1s.terminal.editor.AndroidEditorCoordinator(
                this,(application as TerminalApplication).identityStore)
            val paths=DevicePathCoordinator(this,app.identityStore) {editorCoordinator.releaseAndReport()}
            devicePaths.scheduleWithFixedDelay({
                if(active.get() && AgentCoordinator(this).mayRun())runCatching {paths.cycle()}
                    .onFailure {Log.w("AL1S-Paths","observe_failed:${it.javaClass.simpleName}")}
            },0,15,TimeUnit.SECONDS)
            editor.scheduleWithFixedDelay({
                if (active.get() && AgentCoordinator(this).mayRun()) runCatching { editorCoordinator.cycle {paths.cycle()} }
                    .onFailure { Log.w("AL1S-Editor", "reconcile_failed:${it.javaClass.simpleName}") }
            }, 0, 5, TimeUnit.SECONDS)
        }
        execution.scheduleWithFixedDelay({
            if (active.get() && AgentCoordinator(this).mayRun()) runCatching {
                val app = application as TerminalApplication
                MaaExecutionCoordinator(this, app.database, app.identityStore).cycle()
            }.onFailure { Log.w("AL1S-Execution", "reconcile_failed:${it.javaClass.simpleName}") }
        }, 0, 1, TimeUnit.SECONDS)
        controlRecovery.scheduleWithFixedDelay({
            runCatching { com.al1s.terminal.activation.ControlRecovery.reconcile(this) {
                active.get() && AgentCoordinator(this).mayRun()
            } }
        }, 0, 30, TimeUnit.SECONDS)
        reconciliation.scheduleWithFixedDelay(
            { if (AgentCoordinator(this).mayRun()) runCatching { (application as TerminalApplication).syncEngine().runOnce() } },
            0, 15, TimeUnit.SECONDS,
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        active.set(false)
        networkCallback?.let {callback->runCatching {
            getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(callback)
        }}
        if(::reachability.isInitialized)reachability.close()
        reachabilityHttp.dispatcher.executorService.shutdownNow()
        reachabilityHttp.connectionPool.evictAll()
        executor.shutdownNow()
        reconciliation.shutdownNow()
        controlRecovery.shutdownNow()
        execution.shutdownNow()
        editor.shutdownNow()
        setupChecks.shutdownNow()
        devicePaths.shutdownNow()
        quickTests.shutdownNow()
        if (android.os.Build.VERSION.SDK_INT >= 27 && ::editorCoordinator.isInitialized) editorCoordinator.close()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun scheduleHeartbeat(delay: Long) {
        if (!active.get()) return
        executor.schedule({
            if (!active.get() || !AgentCoordinator(this).mayRun()) { stopSelf(); return@schedule }
            try {
                runCatching {reachability.reconcile()}
                heartbeat()
            } catch (error: Exception) {
                policy.failureDelaySeconds()
                Log.w("AL1S-Sync", "heartbeat_failed:${error.javaClass.simpleName.take(40)}")
            } finally {
                if (active.get()) runCatching { scheduleHeartbeat(policy.jitteredDelaySeconds()) }
            }
        }, delay, TimeUnit.SECONDS)
    }

    private fun heartbeat() {
        val online=(application as TerminalApplication).syncEngine().heartbeatOnly()
        if(online)policy.connected() else policy.failureDelaySeconds()
        val summary=if(online)"平台已连接 · 控制激活状态独立检查" else "连接未确认 · 保留身份并重试"
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID,notification(summary))
    }

    private fun connectionWork(action:()->Unit) {
        runCatching {executor.execute {
            if(active.get() && AgentCoordinator(this).mayRun())runCatching(action)
                .onFailure {Log.w("AL1S-Reachability","connection_failed:${it.javaClass.simpleName}")}
        }}
    }

    private fun startReachability() {
        reachability=PlatformReachability({
            com.al1s.terminal.security.TerminalConnectionGate.withConnection {
                val store=(application as TerminalApplication).identityStore
                val identity=store.load()
                val origin=store.configuration()?.first
                if(active.get() && identity!=null && origin!=null)
                    ReachabilityIdentity(origin,identity.terminalId,identity.credential) else null
            }
        },{request,listener->reachabilityHttp.newWebSocket(request,listener)},
            {connectionWork {Log.i("AL1S-Reachability","connected");heartbeat()}})
        val callback=object:ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network:Network) {connectionWork {reachability.reconcile(networkChanged=true)}}
        }
        runCatching {
            getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(callback)
            networkCallback=callback
        }.onFailure {Log.w("AL1S-Reachability","network_callback_failed:${it.javaClass.simpleName}")}
    }

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
