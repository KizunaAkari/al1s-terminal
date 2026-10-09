package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidIndependentRulesTest {
    @Test fun `independent rules are scoped to selected main steps and use jump back`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait","seconds":0},{"action":"wait","seconds":0}],"global_popups":[{"enabled":true,"step_indexes":[2],"template":"close.png","click_mode":"match_center","timeout_seconds":5}]}"""))
        val root=compiled.pipeline.getJSONObject("Root").getJSONArray("next")
        assertEquals(compiled.stepNodes.getValue(0),root.getString(0))
        val next=compiled.pipeline.getJSONObject(compiled.stepNodes.getValue(0)).getJSONArray("next")
        val rule=next.getJSONObject(0)
        assertTrue(rule.getBoolean("jump_back"))
        val node=compiled.pipeline.getJSONObject(rule.getString("name"))
        assertEquals(1,node.getJSONObject("attach").getInt("dsl_step_index"))
        assertTrue(node.getJSONObject("attach").has("popup_key"))
        assertEquals(5,node.getJSONObject("attach").getInt("popup_budget"))
    }
}
