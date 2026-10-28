package com.al1s.terminal.setup

object SetupInspectionPolicy {
    fun conditions(identity: Boolean, notifications: Boolean, paired: Boolean, helper: Boolean,
        secureKeyguard: Boolean, batteryExempt: Boolean): Map<String, String> = linkedMapOf(
        "platform_identity" to state(identity),
        "notifications" to state(notifications),
        "local_pairing" to state(paired),
        "control_helper" to state(helper),
        "secure_keyguard" to state(!secureKeyguard),
        "battery_exemption" to state(batteryExempt),
        "manufacturer_background_policy" to "unverifiable",
    )
    private fun state(ready: Boolean) = if (ready) "ready" else "blocked"
}
