package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidConditionalSkipPassTest {
    @Test fun `image condition preserves the existing priority and configured target`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait_image","template":"main.png","skip_condition":{"enabled":true,"mode":"image","template":"skip.png","threshold":0.9,"skip_to_step_index":3}},{"action":"wait","seconds":0},{"action":"wait","seconds":0}]}"""))
        val choices = compiled.pipeline.getJSONObject("Root").getJSONArray("next")
        val guard = compiled.pipeline.getJSONObject(choices.getString(0))
        assertEquals("skip.png", guard.getString("template"))
        assertEquals(compiled.stepNodes.getValue(0), choices.getString(1))
        assertEquals(compiled.stepNodes.getValue(2), guard.getJSONArray("next").getString(0))
    }
    @Test fun `numeric condition delegates OCR and retains numeric comparison`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait","seconds":0,"skip_condition":{"enabled":true,"mode":"numeric","operator":"gt","value":7,"region":{"x":10,"y":20,"width":30,"height":40}}},{"action":"wait","seconds":0}]}"""))
        val guard = compiled.pipeline.getJSONObject(compiled.pipeline.getJSONObject("Root").getJSONArray("next").getString(0))
        assertEquals("MaaProjectNumericCompare", guard.getString("custom_recognition"))
        assertEquals("gt", guard.getJSONObject("custom_recognition_param").getString("operator"))
        assertEquals(7, guard.getJSONObject("custom_recognition_param").getInt("value"))
    }
}
