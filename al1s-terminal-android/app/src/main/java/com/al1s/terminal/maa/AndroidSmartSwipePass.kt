package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidSmartSwipePass {
    fun apply(compiled:AndroidCompiledModule,script:JSONObject) {
        val alternatives=mutableMapOf<String,String>()
        compiled.stepNodes.forEach { (index,name)->
            val step=script.getJSONArray("steps").getJSONObject(index)
            if(step.getString("action")!="smart_swipe")return@forEach
            val mode=step.optString("mode","until_image");require(mode in setOf("until_image","after_image"))
            val target=compiled.pipeline.getJSONObject(name);val swipeName="${name}_SwipeAction"
            val config=JSONObject(step.getJSONObject("swipe").toString()).put("action","swipe")
            val swipe=AndroidStepNodes.build(config,index) { error("swipe_has_no_template") }
                .put("post_delay",AndroidPipelineCompiler.milliseconds(step.optDouble("wait_after_swipe_seconds",1.0)))
            swipe.getJSONObject("attach").put("dsl_action","smart_swipe")
            if(mode=="after_image") {
                val duration=AndroidPipelineCompiler.milliseconds(step.optDouble("swipe_for_seconds",5.0))
                val cycle=maxOf(1,swipe.getInt("duration")+swipe.getInt("post_delay"))
                val count=maxOf(1,kotlin.math.ceil(duration.toDouble()/cycle).toInt())
                swipe.put("repeat",count).put("repeat_delay",swipe.getInt("post_delay")).put("post_delay",0)
                    .put("next",target.getJSONArray("next"))
                target.put("next",JSONArray().put(swipeName)).put("timeout",2000).put("rate_limit",100)
            } else {
                alternatives[name]=swipeName
                swipe.put("next",JSONArray())
            }
            compiled.pipeline.put(swipeName,swipe)
        }
        compiled.pipeline.keys().forEach { name ->
            val node=compiled.pipeline.getJSONObject(name);val next=node.optJSONArray("next") ?: return@forEach
            val values=JSONArray()
            for(index in 0 until next.length()) {
                val value=next.get(index);values.put(value)
                if(value is String)alternatives[value]?.let { values.put(JSONObject().put("name",it).put("jump_back",true)) }
            }
            node.put("next",values)
        }
    }
}
