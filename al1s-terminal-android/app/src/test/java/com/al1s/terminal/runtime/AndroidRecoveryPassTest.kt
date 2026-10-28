package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidRecoveryPassTest {
    @Test fun nestedRecoveryEntriesResolveInsideTheirOwnFrozenPipeline() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"definition_key":"main","steps":[{"action":"wait","seconds":0,"failure_retry":{"enabled":true,"max_retries":2,"process_script_name":"outer","process_script":{"definition_key":"outer","script_type":"module_process","steps":[{"action":"wait","seconds":0,"failure_retry":{"enabled":true,"max_retries":2,"process_script_name":"inner","process_script":{"definition_key":"inner","script_type":"module_process","steps":[{"action":"wait","seconds":0}]}}}]}}}]}"""))
        fun validate(pipeline:JSONObject) {
            pipeline.keys().forEach {name->
                val node=pipeline.getJSONObject(name)
                if(node.optString("custom_action")=="MaaProjectFailureRetryProcess") {
                    val parameters=node.getJSONObject("custom_action_param")
                    val nested=parameters.getJSONObject("pipeline")
                    assertTrue("missing nested entry: "+parameters.getString("entry"),nested.has(parameters.getString("entry")))
                    validate(nested)
                }
            }
        }
        validate(compiled.pipeline)
    }
    @Test fun `failure recovery has finite retry count immutable script name and returns to original step`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"definition_key":"cafe","steps":[{"action":"wait","seconds":0,"failure_retry":{"enabled":true,"max_retries":2,"process_script_name":"刷新学生位置","process_script":{"definition_key":"refresh","script_type":"module_process","steps":[{"action":"wait","seconds":0.1}]}}}]}"""))
        val main=compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0))
        val retry=compiled.pipeline.getJSONObject(main.getJSONArray("on_error").getString(0))
        assertEquals(2,retry.getInt("max_hit"))
        assertEquals("刷新学生位置",retry.getJSONObject("custom_action_param").getString("process_script_name"))
        val success=compiled.pipeline.getJSONObject(retry.getJSONArray("next").getString(0))
        assertEquals(compiled.stepNodes.getValue(0),success.getJSONArray("next").getString(0))
        assertTrue(retry.getJSONObject("custom_action_param").getJSONObject("pipeline").keys().asSequence().all { it.startsWith("Recovery_") })
    }
}
