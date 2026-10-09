package com.al1s.terminal.runtime

import com.al1s.terminal.broker.DeviceOperationGate
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors

class DeviceOperationGateTest {
    @Test fun `busy helper rejects replacement and keeps current operations available`() {
        val gate = DeviceOperationGate()
        assertFalse(gate.beginReplacement { false })
        assertEquals("available", gate.invoke { "available" })
        assertTrue(gate.beginReplacement { true })
        assertThrows(IllegalStateException::class.java) { gate.invoke { "late input" } }
    }

    @Test fun `replacement cannot cross an in-flight physical operation`() {
        val gate = DeviceOperationGate()
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        val threads = Executors.newFixedThreadPool(2)
        try {
            val input = threads.submit { gate.invoke { entered.countDown(); release.await(2, TimeUnit.SECONDS) } }
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            val replacement = threads.submit<Boolean> { gate.beginReplacement { true } }
            assertFalse(replacement.isDone)
            release.countDown()
            input.get(1, TimeUnit.SECONDS)
            assertTrue(replacement.get(1, TimeUnit.SECONDS))
        } finally { release.countDown(); threads.shutdownNow() }
    }
}
