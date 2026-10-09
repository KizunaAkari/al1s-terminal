package com.al1s.terminal.runtime

import android.content.Context
import android.content.Intent
import android.os.UserManager
import com.al1s.terminal.TerminalApplication

class AgentCoordinator(private val context: Context) {
    private val settings = context.getSharedPreferences("agent-policy", Context.MODE_PRIVATE)

    fun enabled(): Boolean = settings.getBoolean("enabled", false)

    fun enableAndStart(): Boolean {
        check(settings.edit().putBoolean("enabled", true).commit())
        return restore()
    }

    fun stopByUser() {
        check(settings.edit().putBoolean("enabled", false).commit())
        context.stopService(Intent(context, TerminalForegroundService::class.java))
        SyncWorker.cancel(context)
    }

    fun restore(): Boolean {
        if (!mayRun()) return false
        context.startForegroundService(Intent(context, TerminalForegroundService::class.java))
        SyncWorker.schedule(context)
        return true
    }

    fun mayRun(): Boolean {
        if (!enabled() || !context.getSystemService(UserManager::class.java).isUserUnlocked) return false
        val application = context.applicationContext as TerminalApplication
        return application.identityStore.load() != null
    }
}
