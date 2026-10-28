package com.al1s.terminal.runtime

import com.al1s.terminal.maa.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidFailureSkipPassTest {
    @Test fun `step two recognition failure guard jumps to actual step six`() {
        val script = JSONObject("""{"definition_key":"main","steps":[{"action":"wait","seconds":0},{"action":"wait_image","template":"target.png","skip_condition":{"enabled":true,"mode":"recognition_failure","skip_to_step_index":6}},{"action":"wait","seconds":0},{"action":"wait","seconds":0},{"action":"wait","seconds":0},{"action":"wait","seconds":0}]}""")
        val compiled = AndroidPipelineCompiler.compile(script)
        val guardName = "Step_001_SkipIfFailure"
        val guard = compiled.pipeline.getJSONObject(guardName)
        assertEquals(1, guard.getJSONObject("custom_recognition_param").getInt("step_index"))
        assertEquals("recognition", guard.getJSONObject("custom_recognition_param").getString("phase"))
        assertEquals(compiled.stepNodes.getValue(5), guard.getJSONArray("next").getString(0))
        assertTrue(compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0)).getJSONArray("on_error").toString().contains(guardName))
    }
    @Test(expected = IllegalArgumentException::class)
    fun `failure skip target cannot move backward or to itself`() {
        AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait","seconds":0,"skip_condition":{"enabled":true,"mode":"execution_failure","skip_to_step_index":1}}]}"""))
    }
}
