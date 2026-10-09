package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidPipelineCompilerTest {
    @Test fun `system keys preserve pre and post waiting`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"back","timeout_seconds":20,"wait_before_execution_seconds":3,"wait_after_execution_seconds":1}]}"""))
        val step = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        assertEquals("ClickKey", step.getString("action"))
        assertEquals(4, step.getInt("key"))
        assertEquals(3000, step.getInt("pre_delay"))
        assertEquals(1000, step.getInt("post_delay"))
    }
    @Test fun `matched image center retains actual match and never becomes old fixed coordinates`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"recognize_execute","recognition_mode":"image","template":"target.png","execution_mode":"match_center","execution_count":2,"execution_interval_ms":5000}]}"""))
        val step = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        assertEquals("TemplateMatch", step.getString("recognition"))
        assertTrue(step.getJSONObject("attach").getBoolean("click_match_center"))
        assertEquals("Al1sRepeatedClick", step.getString("custom_action"))
        assertTrue(step.getJSONObject("custom_action_param").getBoolean("recheck_match"))
        assertEquals(5000, step.getJSONObject("custom_action_param").getInt("interval_ms"))
    }
    @Test fun `ordinary wait uses milliseconds and next graph has terminal node`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait","seconds":1.5}]}"""))
        assertEquals(1500, compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0)).getInt("post_delay"))
        assertEquals(0, compiled.pipeline.getJSONObject("End").getJSONArray("next").length())
    }
    @Test(expected = IllegalArgumentException::class)
    fun `refuse unsupported semantics instead of compiling an empty success`() {
        AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"arbitrary_shell"}]}"""))
    }

    @Test fun `OCR expected text uses the native regular expression dialect`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait_text","text":"确认(1)"}]}"""))
        val expected = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0)).getJSONArray("expected").getString(0)
        assertEquals("确认\\(1\\)", expected)
    }

    @Test fun `cold launch force stops before starting and waits after launch`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"launch_app","package":"com.example.game","wait_seconds":2}]}"""))
        val start = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        assertEquals("StartApp", start.getString("action"))
        assertEquals(2000, start.getInt("post_delay"))
        val entry = compiled.pipeline.getJSONObject("Root").getJSONArray("next").getString(0)
        assertEquals("StopApp", compiled.pipeline.getJSONObject(entry).getString("action"))
    }

    @Test fun `native target size is preserved in repeated-click and frame guards`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"target":{"screen_size":{"width":1080,"height":2400}},"steps":[{"action":"tap","x":20,"y":30,"click_count":2}]}"""))
        val step = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        assertEquals("Al1sScreenSizeGuard", step.getString("custom_recognition"))
        assertEquals(1080, step.getJSONObject("custom_recognition_param").getInt("width"))
        assertEquals(2400, step.getJSONObject("custom_action_param").getJSONArray("size").getInt(1))
    }

    @Test fun `stable image wait is compiled as sampled consecutive recognition`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait_image","template":"target.png","consecutive_match_count":3}]}"""))
        val step = compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        assertEquals("Al1sImageMatchStreak", step.getString("custom_recognition"))
        assertEquals(3, step.getJSONObject("custom_recognition_param").getInt("consecutive_match_count"))
    }

    @Test fun `even one match-center click goes through precise center selection`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"recognize_execute","recognition_mode":"image","template":"target.png","execution_mode":"match_center"}]}"""))
        assertEquals("Al1sRepeatedClick", compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0)).getString("custom_action"))
    }

    @Test fun `registered screenshot feedback and start map to real callbacks`() {
        val compiled = AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"start"},{"action":"screenshot"},{"action":"feedback","subject":"完成","message":"已执行"},{"action":"cleanup"}]}"""))
        assertEquals("MaaProjectStart", compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0)).getString("custom_action"))
        assertEquals("MaaProjectScreenshot", compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(1)).getString("custom_action"))
        assertEquals("MaaProjectFeedback", compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(2)).getString("custom_action"))
        assertEquals("DoNothing", compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(3)).getString("action"))
    }
}
