package com.al1s.terminal.maa

import org.json.JSONObject

object AndroidImageExecutionPass {
    fun apply(compiled:AndroidCompiledModule,script:JSONObject,materialize:(String)->String) {
        compiled.stepNodes.forEach { (index,name)->
            val step=script.getJSONArray("steps").getJSONObject(index)
            if(step.optString("action")!="recognize_execute" || step.optString("execution_mode")!="image_center")return@forEach
            val source="${name}_ImageExecutionSource"
            val target=JSONObject(step.toString()).put("action","wait_click").put("click_mode","match_center")
                .put("template_base64",step.getString("click_template_base64"))
                .put("threshold",step.optDouble("click_threshold",step.optDouble("threshold",0.85)))
            step.optJSONObject("click_search_region")?.let {target.put("search_region",it)}
            compiled.pipeline.put(source,AndroidStepNodes.build(target,index,materialize).put("repeat",1)
                .put("repeat_delay",0).put("pre_delay",0).put("post_delay",0))
            compiled.pipeline.getJSONObject(name).put("custom_action_param",JSONObject().put("source",source)
                .put("count",step.optInt("execution_count",1))
                .put("interval_ms",step.optInt("execution_interval_ms",120))
                .put("poll_ms",AndroidPipelineCompiler.milliseconds(step.optDouble("poll_interval_seconds",1.0))))
        }
    }
}
