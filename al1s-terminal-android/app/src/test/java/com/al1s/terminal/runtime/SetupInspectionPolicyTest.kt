package com.al1s.terminal.runtime

import com.al1s.terminal.setup.SetupInspectionPolicy
import org.junit.Assert.*
import org.junit.Test

class SetupInspectionPolicyTest {
    @Test fun `registered online cannot replace control or keyguard readiness`() {
        val conditions = SetupInspectionPolicy.conditions(identity = true, notifications = true, paired = true,
            helper = false, secureKeyguard = true, batteryExempt = false)
        assertEquals("ready", conditions.getValue("platform_identity"))
        assertEquals("blocked", conditions.getValue("control_helper"))
        assertEquals("blocked", conditions.getValue("secure_keyguard"))
        assertEquals("unverifiable", conditions.getValue("manufacturer_background_policy"))
    }
    @Test fun `successful mandatory facts retain vendor policy as unverifiable`() {
        val conditions = SetupInspectionPolicy.conditions(true, true, true, true, false, true)
        assertTrue(conditions.filterKeys { it != "manufacturer_background_policy" }.values.all { it == "ready" })
    }
}
