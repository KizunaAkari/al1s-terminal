package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidMatchLoopPass {
    fun target(step:JSONObject,index:Int,materialize:(String)->String):JSONObject {
        val base=AndroidStepNodes.build(JSONObject(step.toString()).put("action","wait_image"),index,materialize)
        val rect=step.getJSONObject("template_rect");val width=rect.getInt("width");val height=rect.getInt("height")
        require(width>0 && height>0)
        val dx=step.optInt("match_offset_x",0);val dy=step.optInt("match_offset_y",0)
        require(dx in -4096..4096 && dy in -4096..4096)
        val anchor=when(step.optString("match_anchor","center")) {
            "top_left"->0 to 0;"top_right"->width-1 to 0;"bottom_left"->0 to height-1
            "bottom_right"->width-1 to height-1;"center"->(width-1)/2 to (height-1)/2
            else->throw IllegalArgumentException("match_anchor_invalid")
        }
        val count=step.optInt("match_max_clicks",50);require(count in 1..200)
        base.put("action","Click").put("target",true).put("target_offset",JSONArray().put(anchor.first+dx).put(anchor.second+dy)
            .put(1-width).put(1-height)).put("max_hit",count)
            .put("post_delay",AndroidPipelineCompiler.milliseconds(step.optDouble("wait_after_click_seconds",0.7)))
        base.getJSONObject("attach").put("dsl_action","wait_click")
        return base
    }

    fun apply(compiled:AndroidCompiledModule,script:JSONObject,materialize:(String)->String) {
        val loops=mutableMapOf<String,String>()
        compiled.stepNodes.forEach { (index,name)->
            val step=script.getJSONArray("steps").getJSONObject(index)
            if(step.getString("action")!="wait_click" || step.optString("click_mode")!="match_offset")return@forEach
            val node=compiled.pipeline.getJSONObject(name);val raw=target(step,index,materialize)
            val source="${name}_OffsetSource";val doneName="${name}_MatchDone";val limitName="${name}_MatchLimit"
            compiled.pipeline.put(source,JSONObject(raw.toString()).put("action","DoNothing").put("next",JSONArray())
                .put("order_by",step.optString("match_order","Vertical")).put("index",0))
            node.put("recognition","Custom").put("custom_recognition","MaaProjectMatchOffset")
                .put("custom_recognition_param",JSONObject().put("source",source).put("preferred_index",step.optInt("match_index",1)-1))
            compiled.pipeline.put(doneName,JSONObject().put("recognition","DirectHit").put("action","DoNothing")
                .put("next",node.getJSONArray("next")).put("pre_delay",0).put("post_delay",0).put("attach",JSONObject(raw.getJSONObject("attach").toString())))
            compiled.pipeline.put(limitName,JSONObject(raw.toString()).put("action","Custom").put("custom_action","MaaProjectMatchLoopLimit")
                .put("custom_action_param",JSONObject().put("max_clicks",raw.getInt("max_hit")).put("step_index",index))
                .put("max_hit",2147483647).put("next",JSONArray()))
            node.put("next",JSONArray().put(name).put(limitName).put(doneName)).put("timeout",1000)
                .put("rate_limit",AndroidPipelineCompiler.milliseconds(step.optDouble("poll_interval_seconds",1.0)))
            loops[name]=doneName
        }
        compiled.pipeline.keys().forEach { name->
            if(name in loops || name.endsWith("Source"))return@forEach
            val node=compiled.pipeline.getJSONObject(name);val next=node.optJSONArray("next") ?: return@forEach
            val values=JSONArray()
            for(i in 0 until next.length()) {val value=next.get(i);values.put(value);if(value is String)loops[value]?.let {values.put(it)}}
            node.put("next",values)
        }
    }
}
