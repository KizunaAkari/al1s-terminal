package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.Rule
import org.junit.rules.TemporaryFolder

class AndroidIndependentRulesTest {
    @get:Rule val temporary = TemporaryFolder()

    @Test fun `text popup can click a fixed point`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait","seconds":0}],"global_popups":[{"recognition_mode":"text","text":"追加","click_mode":"fixed","click":{"x":30,"y":40}}]}"""))
        val nodes=compiled.pipeline.keys().asSequence().map { compiled.pipeline.getJSONObject(it) }.toList()
        assertTrue(nodes.any { it.optString("recognition")=="OCR" && it.optJSONArray("target")?.toString()=="[30,40]" })
    }

    @Test fun `text trigger keeps its region separate from an image click target`() {
        val image="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPj8AAAAASUVORK5CYII="
        val script=JSONObject("""{"steps":[{"action":"wait","seconds":0}],"global_popups":[{"recognition_mode":"text","text":"追加","click_mode":"image","search_region":{"x":10,"y":20,"width":200,"height":70},"click_search_region":{"x":300,"y":400,"width":100,"height":40}}]}""")
        script.getJSONArray("global_popups").getJSONObject(0).put("click_template_base64",image)
        val compiled=AndroidPipelineCompiler.compile(script,temporary.newFolder())
        val nodes=compiled.pipeline.keys().asSequence().map { compiled.pipeline.getJSONObject(it) }.toList()
        assertTrue(nodes.any { it.optString("recognition")=="OCR" && it.optJSONArray("roi")?.toString()=="[10,20,200,70]" })
        assertTrue(nodes.any { it.optString("recognition")=="TemplateMatch" && it.optJSONArray("roi")?.toString()=="[300,400,100,40]" })
    }
    @Test fun `text popup condition uses literal OCR and can click matched text`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait","seconds":0}],"global_popups":[{"recognition_mode":"text","text":"追加(1)+","click_mode":"match_center","search_region":{"x":10,"y":20,"width":200,"height":70}}]}"""))
        val sources=compiled.pipeline.keys().asSequence().map { compiled.pipeline.getJSONObject(it) }.filter { it.optString("recognition")=="OCR" }.toList()
        assertTrue(sources.isNotEmpty())
        assertTrue(sources.any { it.optString("action")=="Click" && it.optBoolean("target") })
        sources.forEach { assertEquals("追加\\(1\\)\\+",it.getJSONArray("expected").getString(0));assertEquals("[10,20,200,70]",it.getJSONArray("roi").toString()) }
    }
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
