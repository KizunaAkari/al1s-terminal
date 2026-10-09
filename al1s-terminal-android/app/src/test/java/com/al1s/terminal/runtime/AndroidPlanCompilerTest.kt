package com.al1s.terminal.runtime

import com.al1s.terminal.maa.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidPlanCompilerTest {
    @Test fun `frozen wrappers are mapped without losing their supplied waits or skip phase`() {
        val step = JSONObject("""{"action_id":"back","parameters":{},"wrappers":[{"kind":"system_key_wait","parameters":{"before_seconds":3,"after_seconds":2}},{"kind":"conditional_skip","parameters":{"enabled":true,"mode":"recognition_failure","skip_to_step_index":6}}]}""")
        val module = AndroidMaaModule("main", "v1", "过程脚本", "module_process", JSONObject(), listOf(step), emptyList(), false)
        val plan = AndroidMaaPlan("script", listOf(module), mapOf("main" to module), emptyMap())
        val script = AndroidPlanCompiler.script(module, plan)
        val actual = script.getJSONArray("steps").getJSONObject(0)
        assertEquals(3, actual.getInt("wait_before_execution_seconds"))
        assertEquals(2, actual.getInt("wait_after_execution_seconds"))
        assertEquals("recognition_failure", actual.getJSONObject("skip_condition").getString("mode"))
    }
    @Test fun `recovery includes the immutable named definition and maximum retry count`() {
        val recovery = AndroidMaaModule("recovery", "v2", "刷新学生位置", "module_process", JSONObject(),
            listOf(JSONObject("""{"action_id":"wait","parameters":{"seconds":1},"wrappers":[]}""")), emptyList(), false)
        val main = AndroidMaaModule("main", "v1", "咖啡厅", "module_process", JSONObject(),
            listOf(JSONObject("""{"action_id":"tap","parameters":{"x":10,"y":20},"wrappers":[{"kind":"failure_retry","parameters":{"recovery_definition_key":"recovery","max_retries":2}}]}""")), emptyList(), false)
        val plan = AndroidMaaPlan("script", listOf(main), mapOf("main" to main, "recovery" to recovery), emptyMap())
        val retry = AndroidPlanCompiler.script(main, plan).getJSONArray("steps").getJSONObject(0).getJSONObject("failure_retry")
        assertEquals(2, retry.getInt("max_retries"))
        assertEquals("刷新学生位置", retry.getString("process_script_name"))
        assertEquals("wait", retry.getJSONObject("process_script").getJSONArray("steps").getJSONObject(0).getString("action"))
    }
}
