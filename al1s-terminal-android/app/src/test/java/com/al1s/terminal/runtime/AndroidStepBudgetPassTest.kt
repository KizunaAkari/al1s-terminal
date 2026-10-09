package com.al1s.terminal.runtime

import com.al1s.terminal.maa.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidStepBudgetPassTest {
    @Test fun `budget wrapping retains original waits while native lookup has no separate resetting timer`() {
        val script = JSONObject("""{"definition_key":"main","steps":[{"action":"back","timeout_seconds":20,"wait_before_execution_seconds":3,"wait_after_execution_seconds":2}]}""")
        val compiled = AndroidPipelineCompiler.compile(script)
        AndroidStepBudgetPass.apply(compiled, script)
        val node = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        assertEquals("Al1sStepBudgetAction", node.getString("custom_action"))
        assertEquals(0, node.getInt("pre_delay"))
        val parameters = node.getJSONObject("custom_action_param")
        assertEquals(3000, parameters.getInt("pre_delay"))
        assertEquals(2000, parameters.getInt("post_delay"))
        assertEquals("main:0", parameters.getString("key"))
        assertEquals(-1, compiled.pipeline.getJSONObject(compiled.entry).getInt("timeout"))
    }
}
