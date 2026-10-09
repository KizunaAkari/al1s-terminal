package com.al1s.terminal.runtime

import com.al1s.terminal.maa.AndroidPipelineCompiler
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidMatchLoopTest {
    @Test fun `match offset applies selected anchor and loops until the target disappears`() {
        val compiled=AndroidPipelineCompiler.compile(JSONObject("""{"steps":[{"action":"wait_click","click_mode":"match_offset","template":"target.png","template_rect":{"x":0,"y":0,"width":20,"height":30},"match_anchor":"top_left","match_offset_x":45,"match_offset_y":110,"match_max_clicks":32}]}"""))
        val name=compiled.stepNodes.getValue(0);val node=compiled.pipeline.getJSONObject(name)
        assertEquals("MaaProjectMatchOffset",node.getString("custom_recognition"))
        assertEquals(45,node.getJSONArray("target_offset").getInt(0))
        assertEquals(-19,node.getJSONArray("target_offset").getInt(2))
        assertEquals(name,node.getJSONArray("next").getString(0))
        assertEquals("End",compiled.pipeline.getJSONObject("${name}_MatchDone").getJSONArray("next").getString(0))
    }
}
