package com.al1s.terminal.runtime

import com.al1s.terminal.maa.*
import org.junit.Assert.*
import org.junit.Test

class NativeHandlersTest {
    private class MovingTarget : MaaContextAccess {
        val clicks = mutableListOf<IntArray>()
        var captured = 0
        var released = 0
        var cancelled = false
        override fun recognize(context: Long, source: String, image: Long) = NativeRecognitionResult(true, intArrayOf(300, 400, 20, 20), "{}")
        override fun action(context: Long, source: String, box: IntArray, detail: String): Boolean { clicks += box.copyOf(); return true }
        override fun capture(context: Long): Long { captured++; return captured.toLong() }
        override fun imageSize(image: Long) = intArrayOf(1080, 2400)
        override fun destroyImage(image: Long) { released++ }
        override fun nodeData(context: Long, source: String) = """{"attach":{"click_match_center":true}}"""
        override fun stopping(context: Long) = cancelled
    }

    @Test fun `second click uses a fresh detected location and frees the frame`() {
        val device = MovingTarget()
        val handler = NativeHandlers(MaaEventProjection(), device)
        assertTrue(handler.act(1, 1, "step", "Al1sRepeatedClick",
            """{"source":"source","count":"2","interval_ms":0,"budget_seconds":3,"size":[1080,2400],"recheck_match":true}""", 1, intArrayOf(100, 200, 20, 20)))
        assertEquals(2, device.clicks.size)
        assertArrayEquals(intArrayOf(109, 209, 1, 1), device.clicks[0])
        assertArrayEquals(intArrayOf(309, 409, 1, 1), device.clicks[1])
        assertEquals(1, device.captured)
        assertEquals(device.captured, device.released)
    }

    @Test fun `cancelled execution cannot inject a first or late click`() {
        val device = MovingTarget().apply { cancelled = true }
        val handler = NativeHandlers(MaaEventProjection(), device)
        assertFalse(handler.act(1, 1, "step", "Al1sRepeatedClick",
            """{"source":"source","count":"2","interval_ms":0,"budget_seconds":3}""", 1, intArrayOf(100, 200, 20, 20)))
        assertTrue(device.clicks.isEmpty())
    }
}
