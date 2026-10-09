package com.al1s.terminal.setup

import android.Manifest
import android.app.KeyguardManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import com.al1s.terminal.TerminalApplication
import com.al1s.terminal.activation.PairingIdentityStore
import com.al1s.terminal.broker.BrokerClient
import org.json.JSONObject

/** Explicit inspection only; heartbeat does not call this scanner. */
object SetupInspector {
    fun inspect(context: Context): JSONObject {
        val application = context.applicationContext as TerminalApplication
        val power = context.getSystemService(PowerManager::class.java)
        val keyguard = context.getSystemService(KeyguardManager::class.java)
        val conditions = SetupInspectionPolicy.conditions(application.identityStore.load() != null,
            Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED,
            PairingIdentityStore(context).hasIdentity(), BrokerClient.connected(), keyguard.isDeviceSecure,
            power.isIgnoringBatteryOptimizations(context.packageName))
        return JSONObject().put("schema_version", 1).put("checked_at", java.time.Instant.now().toString())
            .put("conditions", JSONObject(conditions)).put("screen_interactive", power.isInteractive)
            .put("keyguard_locked", keyguard.isKeyguardLocked).put("manufacturer", Build.MANUFACTURER)
            .put("android_version", Build.VERSION.RELEASE)
    }
}
