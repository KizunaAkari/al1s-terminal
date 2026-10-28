package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

object AndroidRecoveryPass {
    fun apply(compiled:AndroidCompiledModule,script:JSONObject,imageDirectory:File?,entries:Map<Int,String>,resources:Map<String,File> = emptyMap()) {
        compiled.stepNodes.forEach { (index,name)->
            val config=script.getJSONArray("steps").getJSONObject(index).optJSONObject("failure_retry") ?: return@forEach
            if(config.opt("enabled")!=true)return@forEach
            val count=config.opt("max_retries") ?: 2;require(count is Int && count in 1..20)
            val recovery=config.getJSONObject("process_script");require(recovery.optString("script_type")=="module_process")
            val nested=AndroidPipelineCompiler.compile(recovery,imageDirectory,resources)
            AndroidStepBudgetPass.apply(nested,recovery)
            val isolated=PipelineNamespace.rename(nested,"Recovery_${index}_")
            isolated.pipeline.keys().forEach { compiled.pipeline.put(it,isolated.pipeline.get(it)) }
            val retry="${name}_FailureRetry";val limit="${name}_FailureRetryLimit";val success="${name}_RecoverySucceeded"
            val targetSucceeded="${name}_TargetSucceeded"
            val reset=compiled.pipeline.keys().asSequence().filter { it.startsWith(name) && compiled.pipeline.getJSONObject(it).has("max_hit") }.toList()
            compiled.pipeline.put(retry,JSONObject().put("recognition","DirectHit").put("action","Custom")
                .put("custom_action","MaaProjectFailureRetryProcess").put("max_hit",count)
                .put("custom_action_param",JSONObject().put("entry",isolated.entry).put("pipeline",isolated.pipeline)
                    .put("process_script_name",config.getString("process_script_name")).put("target_step_index",index)
                    .put("reset_hit_count_nodes",JSONArray(reset)))
                .put("next",JSONArray().put(success)).put("on_error",JSONArray().put(retry).put(limit))
                .put("attach",JSONObject().put("dsl_step_index",index).put("dsl_action","failure_retry_process").put("maa_project_role","failure-retry-process")))
            compiled.pipeline.put(limit,JSONObject().put("recognition","DirectHit").put("action","Custom")
                .put("custom_action","MaaProjectFailureRetryLimit").put("custom_action_param",JSONObject().put("max_retries",count).put("target_step_index",index))
                .put("next",JSONArray()).put("attach",JSONObject().put("dsl_step_index",index).put("maa_project_role","failure-retry-limit")))
            compiled.pipeline.put(success,JSONObject().put("recognition","DirectHit").put("action","Custom")
                .put("custom_action","Al1sRecoverySucceeded").put("custom_action_param",JSONObject().put("key","${script.optString("definition_key","script")}:$index"))
                .put("next",JSONArray().put(entries.getValue(index))).put("on_error",JSONArray().put(retry).put(limit)))
            val exit=listOf("${name}_Assert","${name}_ColorDone","${name}_MatchDone",name).first { compiled.pipeline.has(it) }
            val exitNode=compiled.pipeline.getJSONObject(exit);val next=exitNode.getJSONArray("next")
            compiled.pipeline.put(targetSucceeded,JSONObject().put("recognition","DirectHit").put("action","Custom")
                .put("custom_action","Al1sRetrySucceeded").put("custom_action_param",JSONObject().put("retry_entry",retry))
                .put("next",next))
            exitNode.put("next",JSONArray().put(targetSucceeded))
            compiled.pipeline.keys().asSequence().filter { it.startsWith(name) && !it.endsWith("Source") && it !in setOf(retry,limit,targetSucceeded) }.toList().forEach { nodeName ->
                val node=compiled.pipeline.getJSONObject(nodeName);val errors=node.optJSONArray("on_error") ?: JSONArray()
                val current=JSONArray();for(i in 0 until errors.length())current.put(errors.get(i))
                current.put(retry).put(limit);node.put("on_error",current)
            }
        }
    }
}
