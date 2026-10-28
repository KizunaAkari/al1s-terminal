package com.al1s.terminal.runtime

import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Test

class AgentReconciliationGateTest {
    @Test fun workerAndForegroundServiceCannotReceiveTheSameQueueConcurrently() {
        val entered = CountDownLatch(1)
        val finish = CountDownLatch(1)
        val pool = Executors.newSingleThreadExecutor()
        try {
            val first = pool.submit<String?> {
                AgentReconciliationGate.tryRun { entered.countDown(); finish.await(3, TimeUnit.SECONDS); "first" }
            }
            assertTrue(entered.await(2, TimeUnit.SECONDS))
            assertNull(AgentReconciliationGate.tryRun { "duplicate" })
            finish.countDown()
            assertEquals("first", first.get(2, TimeUnit.SECONDS))
            assertEquals("later", AgentReconciliationGate.tryRun { "later" })
        } finally { finish.countDown(); pool.shutdownNow() }
    }

    @Test fun networkFailureReleasesTheGateForTheNextReconciliation() {
        runCatching { AgentReconciliationGate.tryRun<String> { error("network interrupted") } }
        assertEquals("retry", AgentReconciliationGate.tryRun { "retry" })
    }
}
