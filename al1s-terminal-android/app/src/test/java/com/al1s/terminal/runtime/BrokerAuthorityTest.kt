package com.al1s.terminal.runtime

import com.al1s.terminal.broker.BrokerAuthority
import com.al1s.terminal.activation.LocalAdbEndpoint
import com.al1s.terminal.activation.WirelessRecoveryPolicy
import org.junit.Assert.*
import org.junit.Test

class BrokerAuthorityTest {
    @Test fun pairingIsLimitedToLoopbackAndPortsMustBeValid() {
        assertTrue(LocalAdbEndpoint.accepts("127.0.0.1", 37001))
        assertTrue(LocalAdbEndpoint.accepts("::1", 37002))
        assertFalse(LocalAdbEndpoint.accepts("192.168.1.9", 37001))
        assertFalse(LocalAdbEndpoint.accepts("127.0.0.1", 0))
    }
    @Test fun wrongUidEpochAndExpiredControlAreRejected() {
        val guard = BrokerAuthority(appUid = 10123, epoch = "epoch-1", secret = "x".repeat(48))
        assertTrue(guard.accepts(10123, "epoch-1", "x".repeat(48), 110, 100))
        assertFalse(guard.accepts(10124, "epoch-1", "x".repeat(48), 110, 100))
        assertFalse(guard.accepts(10123, "epoch-2", "x".repeat(48), 110, 100))
        assertFalse(guard.accepts(10123, "epoch-1", "x".repeat(48), 100, 100))
    }
    @Test fun wirelessRecoveryNeverRunsWithoutSeparateConsentAndPermission() {
        assertFalse(WirelessRecoveryPolicy.shouldEnable(false, true, true))
        assertFalse(WirelessRecoveryPolicy.shouldEnable(true, false, true))
        assertFalse(WirelessRecoveryPolicy.shouldEnable(true, true, false))
        assertTrue(WirelessRecoveryPolicy.shouldEnable(true, true, true))
    }
}
