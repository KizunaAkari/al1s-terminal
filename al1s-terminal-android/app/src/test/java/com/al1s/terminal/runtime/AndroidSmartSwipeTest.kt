package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidSmartSwipeTest {
    @Test fun `until-image swipe returns to the same recognition list`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"smart_swipe","template":"target.png","mode":"until_image","swipe":{"x1":10,"y1":20,"x2":30,"y2":40,"duration_ms":350}}]}"""))
        val choices=compiled.pipeline.getJSONObject("Root").getJSONArray("next")
        assertEquals(compiled.stepNodes.getValue(0),choices.getString(0))
        val swipe=choices.getJSONObject(1)
        assertTrue(swipe.getBoolean("jump_back"))
        assertEquals("Swipe",compiled.pipeline.getJSONObject(swipe.getString("name")).getString("action"))
    }
}
