package com.al1s.terminal.runtime

import com.al1s.terminal.maa.NativeFailureState
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NativeFailureStateTest {
    private fun pipeline() = JSONObject("""{"Root":{"next":["Step2"]},"Step2":{"recognition":"TemplateMatch","attach":{"dsl_step_index":1,"maa_project_role":"step"}},"Rule":{"recognition":"TemplateMatch","attach":{"dsl_step_index":1,"popup_key":"rule-0"}}}""")
    @Test fun `recognition list expiry belongs to target step and not predecessor or independent rule`() {
        val state = NativeFailureState(pipeline())
        state.observe("Node.NextList.Starting", JSONObject("""{"name":"Root","list":["Step2"]}"""))
        state.observe("Node.Recognition.Failed", JSONObject("""{"name":"Rule"}"""))
        state.observe("Node.PipelineNode.Failed", JSONObject("""{"name":"Root","node_details":{}}"""))
        assertTrue(state.matches(1, "recognition"))
        assertFalse(state.matches(0, "recognition"))
        assertFalse(state.matches(1, "execution"))
    }
    @Test fun `action failure skips only the matching execution failure condition`() {
        val state = NativeFailureState(pipeline())
        state.observe("Node.Recognition.Succeeded", JSONObject("""{"name":"Step2"}"""))
        state.observe("Node.Action.Failed", JSONObject("""{"name":"Step2"}"""))
        assertTrue(state.matches(1, "execution"))
        assertFalse(state.matches(1, "recognition"))
        assertTrue(state.consume(1, "execution"))
        assertFalse(state.matches(1, "execution"))
    }
}
