package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidColorLoopPass {
    fun target(step:JSONObject,index:Int):JSONObject {
        val count=step.optInt("match_max_clicks",50);val absence=step.optInt("marker_absence_checks",3)
        require(count in 1..200 && absence in 2..10)
        val config=JSONObject().put("state_key","color:$index").put("preferred_index",step.optInt("match_index",1)-1)
            .put("offset_x",step.optInt("match_offset_x",0)).put("offset_y",step.optInt("match_offset_y",0))
            .put("anchor",step.optString("match_anchor","center")).put("absence_checks",absence)
            .put("component_min_area",step.optInt("marker_component_min_area",250))
            .put("component_max_area",step.optInt("marker_component_max_area",1800))
            .put("group_distance",step.optInt("marker_group_distance",115)).put("order_by",step.optString("match_order","Vertical"))
            .put("hsv_lower",step.optJSONArray("marker_hsv_lower") ?: JSONArray().put(10).put(160).put(200))
            .put("hsv_upper",step.optJSONArray("marker_hsv_upper") ?: JSONArray().put(40).put(255).put(255)).put("mode","target")
        val wait=step.optDouble("wait_after_click_seconds",0.7);require(wait.isFinite() && wait in 0.1..10.0)
        val node=JSONObject().put("recognition","Custom").put("custom_recognition","MaaProjectColorMarker")
            .put("custom_recognition_param",config).put("action","Click").put("target",true).put("max_hit",count)
            .put("pre_delay",0).put("post_delay",AndroidPipelineCompiler.milliseconds(wait)).put("repeat",1).put("next",JSONArray())
            .put("attach",JSONObject().put("dsl_step_index",index).put("dsl_action","wait_click").put("maa_project_role","step"))
        step.optJSONObject("search_region")?.let { node.put("roi",JSONArray().put(it.getInt("x")).put(it.getInt("y"))
            .put(it.getInt("width")).put(it.getInt("height"))) }
        return node
    }

    fun apply(compiled:AndroidCompiledModule,script:JSONObject) {
        val loops=mutableMapOf<String,String>()
        compiled.stepNodes.forEach { (index,name)->
            val step=script.getJSONArray("steps").getJSONObject(index)
            if(step.getString("action")!="wait_click" || step.optString("click_mode")!="color_marker")return@forEach
            val target=compiled.pipeline.getJSONObject(name)
            val doneName="${name}_ColorDone";val limitName="${name}_ColorLimit"
            val raw=target(step,index)
            val config=raw.getJSONObject("custom_recognition_param")
            config.put("state_key","color:${script.optString("definition_key","script")}:$index")
            target.put("custom_recognition_param",JSONObject(config.toString()))
            val done=JSONObject(raw.toString()).put("next",target.getJSONArray("next")).put("action","DoNothing").put("max_hit",2147483647).put("post_delay",
                AndroidPipelineCompiler.milliseconds(step.optDouble("wait_after_execution_seconds",0.0)))
            done.put("custom_recognition_param",JSONObject(config.toString()).put("mode","absent"))
            val limit=JSONObject(raw.toString()).put("action","Custom").put("custom_action","MaaProjectMatchLoopLimit")
                .put("custom_action_param",JSONObject().put("max_clicks",target.getInt("max_hit")).put("step_index",index))
                .put("max_hit",2147483647).put("next",JSONArray())
            compiled.pipeline.put(doneName,done).put(limitName,limit)
            target.put("next",JSONArray().put(name).put(limitName).put(doneName))
                .put("timeout",-1).put("rate_limit",AndroidPipelineCompiler.milliseconds(step.optDouble("poll_interval_seconds",1.0)))
            loops[name]=doneName
        }
        compiled.pipeline.keys().forEach { name->
            if(name in loops || name.endsWith("Source"))return@forEach
            val node=compiled.pipeline.getJSONObject(name);val next=node.optJSONArray("next") ?: return@forEach
            val changed=JSONArray()
            for(i in 0 until next.length()) {val value=next.get(i);changed.put(value);if(value is String)loops[value]?.let { changed.put(it) }}
            node.put("next",changed)
        }
    }
}
