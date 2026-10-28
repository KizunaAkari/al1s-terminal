package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidDefinitionLoader
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidDefinitionLoaderTest {
    private fun definition() = JSONObject("""{"schema_version":1,"compiler_version":"maa-registered-actions-v2","script_version_id":"v1","script_name":"课程表脚本","script_type":"module_process","target":{},"steps":[{"step_index":1,"action_id":"wait","handler_id":"maa.pipeline.wait","parameters":{"seconds":1},"wrappers":[]}],"independent_rules":[],"cleanup_on_finish":false}""")
    private fun manifest() = JSONObject().put("schema_version", 1).put("definition_type", "script")
        .put("entry_definition_key", "main").put("definitions", JSONObject().put("main", definition()))

    @Test fun `preserve Linux registered definition identity and step order`() {
        val plan = AndroidDefinitionLoader.load(manifest(), emptyMap())
        assertEquals("main", plan.modules.single().definitionKey)
        assertEquals("课程表脚本", plan.modules.single().scriptName)
        assertEquals("wait", plan.modules.single().steps.single().getString("action_id"))
    }
    @Test fun `strategy uses declared positions and preserves module waits`() {
        val manifest = manifest().put("definition_type", "strategy").put("modules",
            org.json.JSONArray("""[{"position":1,"definition_key":"second","wait_after_ms":200},{"position":0,"definition_key":"main","wait_after_ms":100}]"""))
        manifest.getJSONObject("definitions").put("second", definition())
        val plan = AndroidDefinitionLoader.load(manifest, emptyMap())
        assertEquals(listOf("main", "second"), plan.modules.map { it.definitionKey })
        assertEquals(listOf(100, 200), plan.modules.map { it.waitAfterMs })
    }
    @Test(expected = IllegalArgumentException::class)
    fun `reject unknown handler before executing or accepting a package`() {
        val manifest = manifest()
        manifest.getJSONObject("definitions").getJSONObject("main").getJSONArray("steps").getJSONObject(0)
            .put("handler_id", "maa.pipeline.shell")
        AndroidDefinitionLoader.load(manifest, emptyMap())
    }
    @Test(expected = IllegalArgumentException::class)
    fun `reject missing resource in nested wrapper`() {
        val manifest = manifest()
        manifest.getJSONObject("definitions").getJSONObject("main").getJSONArray("steps").getJSONObject(0)
            .getJSONArray("wrappers").put(JSONObject("""{"kind":"post_assertion","handler_id":"maa.wrapper.post_assertion","parameters":{"template":{"${'$'}resource":"missing"}}}"""))
        AndroidDefinitionLoader.load(manifest, emptyMap())
    }
    @Test(expected = IllegalArgumentException::class)
    fun `boolean or duplicate positions cannot silently change order`() {
        AndroidDefinitionLoader.load(manifest().put("definition_type", "strategy")
            .put("modules", org.json.JSONArray("""[{"position":true,"definition_key":"main"}]""")), emptyMap())
    }

    @Test fun `platform specialized failure skip and text assertion retain generic wrapper kinds`() {
        val manifest = manifest()
        val wrappers = manifest.getJSONObject("definitions").getJSONObject("main").getJSONArray("steps")
            .getJSONObject(0).getJSONArray("wrappers")
        wrappers.put(JSONObject("""{"kind":"conditional_skip","handler_id":"maa.wrapper.failure_skip","parameters":{"mode":"recognition_failure"}}"""))
        wrappers.put(JSONObject("""{"kind":"post_assertion","handler_id":"maa.wrapper.post_assertion_text","parameters":{"recognition_mode":"text","text":"确认"}}"""))
        assertEquals(1, AndroidDefinitionLoader.load(manifest, emptyMap()).modules.size)
    }
}
