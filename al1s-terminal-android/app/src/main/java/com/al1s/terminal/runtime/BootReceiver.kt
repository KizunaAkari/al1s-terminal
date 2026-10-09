package com.al1s.terminal.runtime

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED,
                Intent.ACTION_USER_UNLOCKED)) return
        runCatching { AgentCoordinator(context).restore() }
            .onFailure { Log.w("AL1S-Agent", "restore_deferred:${it.javaClass.simpleName}") }
    }
}
