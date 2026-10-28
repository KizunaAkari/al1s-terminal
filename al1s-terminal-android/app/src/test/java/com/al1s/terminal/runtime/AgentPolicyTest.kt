package com.al1s.terminal.runtime

import org.junit.Assert.*
import org.junit.Test

class AgentPolicyTest {
    @Test fun retryIsBoundedAndSuccessRestoresTheHeartbeatInterval() {
        val policy = AgentRetryPolicy()
        repeat(20) { assertTrue(policy.failureDelaySeconds() in 1..60) }
        assertEquals(60L, policy.failureDelaySeconds())
        policy.connected()
        assertEquals(15L, policy.nextDelaySeconds())
    }

    @Test fun failedConnectionsUseJitterWithoutExceedingTheRecoveryBudget() {
        val policy = AgentRetryPolicy()
        repeat(20) {
            val base = policy.failureDelaySeconds()
            assertTrue(policy.jitteredDelaySeconds(0.0) in 1..base)
            assertTrue(policy.jitteredDelaySeconds(1.0) in 1..60)
        }
        policy.connected()
        assertEquals(15L, policy.jitteredDelaySeconds(0.0))
    }
}
