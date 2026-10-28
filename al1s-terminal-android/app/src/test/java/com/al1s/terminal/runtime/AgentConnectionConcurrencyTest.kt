package com.al1s.terminal.runtime

import com.al1s.terminal.security.TerminalConnectionGate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Test

class AgentConnectionConcurrencyTest {
    @Test fun heartbeatCanReadTheSameConnectionWhileTransfersAreSlow() {
        val entered = CountDownLatch(1)
        val finish = CountDownLatch(1)
        val heartbeat = CountDownLatch(1)
        val pool = Executors.newFixedThreadPool(2)
        try {
            pool.submit { TerminalConnectionGate.withConnection { entered.countDown(); finish.await(4, TimeUnit.SECONDS) } }
            assertTrue(entered.await(2, TimeUnit.SECONDS))
            pool.submit { TerminalConnectionGate.withConnection { heartbeat.countDown() } }
            assertTrue("A slow transfer blocked heartbeat", heartbeat.await(1, TimeUnit.SECONDS))
        } finally { finish.countDown(); pool.shutdownNow() }
    }
}
