package com.al1s.terminal.activation

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
import com.al1s.terminal.broker.BrokerClient

/** Permission grants happen only from the phone's explicit confirmation dialog. */
class WirelessRecovery(private val context: Context) {
    private val preferences = context.getSharedPreferences("wireless-recovery-consent", Context.MODE_PRIVATE)
    fun consented(): Boolean = preferences.getBoolean("enabled", false)
    fun granted(): Boolean = context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    fun enableFromExplicitConfirmation() {
        check(PairingIdentityStore(context).hasIdentity() && BrokerClient.connected()) { "pair_and_activate_first" }
        // Persist user intent before the fixed grant. Failure must not trigger an automatic re-grant.
        check(preferences.edit().putBoolean("enabled", true).commit())
        try {
            check(BrokerClient.invoke("grant_wireless_recovery").getBoolean("granted"))
            check(granted())
        } catch (error: Exception) {
            preferences.edit().putBoolean("enabled", false).commit()
            throw error
        }
    }

    fun disable() { check(preferences.edit().putBoolean("enabled", false).commit()) }

    fun restoreIfAllowed(): Boolean {
        if (!WirelessRecoveryPolicy.shouldEnable(consented(), granted(), PairingIdentityStore(context).hasIdentity())) return false
        return runCatching {
            if (Settings.Global.getInt(context.contentResolver, "adb_wifi_enabled", 0) == 1) true
            else Settings.Global.putInt(context.contentResolver, "adb_wifi_enabled", 1)
        }.getOrDefault(false)
    }
}
