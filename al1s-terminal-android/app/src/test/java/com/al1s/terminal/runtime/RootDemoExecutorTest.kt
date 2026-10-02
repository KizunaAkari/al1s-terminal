package com.al1s.terminal.runtime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RootDemoExecutorTest {
    @Test
    fun executesOnlyTheCompiledRootProbeCommand() {
        var observed: List<String>? = null
        val executor = RootDemoExecutor { command ->
            observed = command
            0
        }

        assertTrue(executor.execute("root_probe").success)
        assertEquals(listOf("su", "-c", "id"), observed)
    }

    @Test
    fun rejectsUnknownActionsWithoutStartingAProcess() {
        var called = false
        val executor = RootDemoExecutor {
            called = true
            0
        }

        val result = executor.execute("shell:rm -rf /data")

        assertFalse(result.success)
        assertEquals("android_demo_action_unsupported", result.errorCode)
        assertFalse(called)
    }
}
