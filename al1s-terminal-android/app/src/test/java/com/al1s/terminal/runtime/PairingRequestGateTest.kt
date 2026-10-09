package com.al1s.terminal.runtime

import com.al1s.terminal.activation.PairingRequestGate
import org.junit.Assert.*
import org.junit.Test

class PairingRequestGateTest {
    @Test fun onlyTheCurrentUnexpiredNotificationCanSubmitOnePairingCode() {
        val gate = PairingRequestGate("current", 100)
        assertFalse(gate.accept("old", "123456", 101))
        assertFalse(gate.accept("current", "12345", 101))
        assertTrue(gate.accept("current", "123456", 101))
        assertFalse(gate.accept("current", "123456", 102))
    }

    @Test fun cancellationAndTimeoutDoNotLeaveAnActivationRequestBehind() {
        val expired = PairingRequestGate("current", 100)
        assertFalse(expired.accept("current", "123456", 280))
        val cancelled = PairingRequestGate("current", 100)
        cancelled.cancel()
        assertFalse(cancelled.accept("current", "123456", 101))
    }
}
