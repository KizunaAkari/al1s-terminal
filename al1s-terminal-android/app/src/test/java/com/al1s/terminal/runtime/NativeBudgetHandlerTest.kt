package com.al1s.terminal.runtime

import com.al1s.terminal.maa.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NativeBudgetHandlerTest {
    @Test fun `expired recognition produces the failure action route rather than another endless miss`() {
        var now = 0L
        val clock = StepBudgetClock { now }
        val handler = NativeBudgetHandler(clock, MaaEventProjection())
        val config = JSONObject("""{"key":"main:1","budget":1,"rule":null,"rule_budget":30,"step_index":1,"source":"source","pre_delay":0,"post_delay":0}""")
        handler.enter(config)
        now = 2_000_000_000L
        val result = handler.recognize(config, "node") { NativeRecognitionResult(false, intArrayOf(0,0,0,0), "{}") }
        assertTrue(result?.hit == true)
        assertFalse(handler.action(config, "node", { false }) { true })
    }

    @Test fun `independent rule timeout is fatal and cannot masquerade as an accepted main-step failure`() {
        var now = 0L
        val handler = NativeBudgetHandler(StepBudgetClock { now }, MaaEventProjection())
        val config = JSONObject("""{"key":"main:1","budget":20,"rule":"rule:0","rule_budget":1,"step_index":1,"source":"source"}""")
        handler.enter(config)
        now = 2_000_000_000L
        assertNull(handler.recognize(config, "rule-node") { NativeRecognitionResult(true, intArrayOf(0,0,1,1), "{}") })
        assertTrue(handler.fatal)
    }
}
