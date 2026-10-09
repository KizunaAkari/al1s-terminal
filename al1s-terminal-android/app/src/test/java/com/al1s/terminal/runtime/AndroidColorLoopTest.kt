package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidColorLoopTest {
    @Test fun `student loop rerecognizes after each click and ends only with stable absence`() {
        val script=JSONObject("""{"steps":[{"action":"wait_click","click_mode":"color_marker","match_anchor":"top_left","match_offset_x":45,"match_offset_y":110,"marker_absence_checks":3,"match_max_clicks":32,"wait_after_click_seconds":1.1},{"action":"wait","seconds":0}]}""")
        val compiled=AndroidPipelineCompiler.compile(script)
        val name=compiled.stepNodes.getValue(0)
        val target=compiled.pipeline.getJSONObject(name)
        assertEquals("MaaProjectColorMarker",target.getString("custom_recognition"))
        assertEquals(32,target.getInt("max_hit"))
        assertEquals(name,target.getJSONArray("next").getString(0))
        assertEquals(1100,target.getInt("post_delay"))
        val done=compiled.pipeline.getJSONObject("${name}_ColorDone")
        assertEquals("absent",done.getJSONObject("custom_recognition_param").getString("mode"))
        assertEquals(compiled.stepNodes.getValue(1),done.getJSONArray("next").getString(0))
    }
    @Test fun `fixed native geometry does not replace color settings in done recognition`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"target":{"screen_size":{"width":2400,"height":1080}},"steps":[{"action":"wait_click","click_mode":"color_marker","marker_absence_checks":3,"match_offset_y":110}]}"""))
        val done=compiled.pipeline.getJSONObject("${compiled.stepNodes.getValue(0)}_ColorDone")
        assertEquals("Al1sScreenSizeGuard",done.getString("custom_recognition"))
        val source=compiled.pipeline.getJSONObject(done.getJSONObject("custom_recognition_param").getString("source"))
        assertEquals("MaaProjectColorMarker",source.getString("custom_recognition"))
        assertEquals(110,source.getJSONObject("custom_recognition_param").getInt("offset_y"))
    }
    @Test fun `post assertion runs after stable absence without replacing the touch loop`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait_click","click_mode":"color_marker","post_assertion":{"enabled":true,"recognition_mode":"text","text":"完成","max_retries":2}}]}"""))
        val name=compiled.stepNodes.getValue(0)
        assertEquals(name,compiled.pipeline.getJSONObject(name).getJSONArray("next").getString(0))
        assertEquals("${name}_Assert",compiled.pipeline.getJSONObject("${name}_ColorDone").getJSONArray("next").getString(0))
    }
}
