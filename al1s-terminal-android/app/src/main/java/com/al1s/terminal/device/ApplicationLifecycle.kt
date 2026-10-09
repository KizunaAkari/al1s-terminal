package com.al1s.terminal.device

import android.app.ActivityManager
import android.app.KeyguardManager
import android.content.Context
import android.os.Bundle
import android.os.PowerManager

class ApplicationLifecycle(private val context: Context) {
    fun foreground(): Bundle {
        @Suppress("DEPRECATION") val task = context.getSystemService(ActivityManager::class.java).getRunningTasks(1).firstOrNull()
        val packageName = checkNotNull(task?.topActivity?.packageName) { "foreground_application_unavailable" }
        val info = context.packageManager.getApplicationInfo(packageName, 0)
        return Bundle().apply {
            putString("package_name", packageName)
            putString("label", context.packageManager.getApplicationLabel(info).toString().take(256))
        }
    }

    fun icon(packageName: String): ByteArray {
        require(packageName.matches(Regex("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+")))
        val icon = context.packageManager.getApplicationIcon(packageName)
        val bitmap = android.graphics.Bitmap.createBitmap(192, 192, android.graphics.Bitmap.Config.ARGB_8888)
        try {
            icon.setBounds(0, 0, 192, 192)
            icon.draw(android.graphics.Canvas(bitmap))
            return java.io.ByteArrayOutputStream().use { bytes ->
                check(bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, bytes))
                bytes.toByteArray()
            }
        } finally { bitmap.recycle() }
    }

    fun settings(): Bundle {
        val keyguard = context.getSystemService(KeyguardManager::class.java)
        val power = context.getSystemService(PowerManager::class.java)
        return Bundle().apply {
            putBoolean("screen_interactive", power.isInteractive)
            putBoolean("keyguard_locked", keyguard.isKeyguardLocked)
            putBoolean("secure_keyguard", keyguard.isDeviceSecure)
            putString("manufacturer_background_policy", "unverifiable")
        }
    }

    fun wake(beforeInput:()->Unit = {}): Bundle {
        ScreenPreparation.prepare(object:ScreenPreparationPort {
            override fun state():ScreenState {
                val current=settings()
                return ScreenState(current.getBoolean("screen_interactive"),current.getBoolean("keyguard_locked"),
                    current.getBoolean("secure_keyguard"),context.getSystemService(android.os.UserManager::class.java).isUserUnlocked)
            }
            override fun wake(timeoutMs:Long){beforeInput();DeviceCommand.run(listOf("/system/bin/input","keyevent","224"),timeoutMillis=timeoutMs)}
            override fun dismiss(timeoutMs:Long){beforeInput();DeviceCommand.run(listOf("/system/bin/wm","dismiss-keyguard"),timeoutMillis=timeoutMs)}
            override fun nowMs()=android.os.SystemClock.elapsedRealtime()
            override fun pause(){Thread.sleep(100)}
        })
        return settings()
    }
}
