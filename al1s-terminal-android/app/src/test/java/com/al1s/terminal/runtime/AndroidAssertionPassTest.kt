package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidAssertionPassTest {
    @Test fun `assertion runs after action wait and bounded retry returns to original step`() {
        val script = JSONObject("""{"steps":[{"action":"tap","x":10,"y":20,"wait_after_execution_seconds":1,"post_assertion":{"enabled":true,"recognition_mode":"image","template":"assert.png","threshold":0.8,"timeout_seconds":3,"max_retries":2}},{"action":"wait","seconds":0}]}""")
        val compiled = AndroidPipelineCompiler.compile(script)
        val main = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        val assertionName = main.getJSONArray("next").getString(0)
        val assertion = compiled.pipeline.getJSONObject(assertionName)
        assertEquals("TemplateMatch", assertion.getString("recognition"))
        assertEquals("assert.png", assertion.getString("template"))
        assertEquals(compiled.stepNodes.getValue(1), assertion.getJSONArray("next").getString(0))
        val retryName = main.getJSONArray("on_error").getString(0)
        val retry = compiled.pipeline.getJSONObject(retryName)
        assertEquals(2, retry.getInt("max_hit"))
        assertEquals(compiled.stepNodes.getValue(0), retry.getJSONArray("next").getString(0))
    }
}
