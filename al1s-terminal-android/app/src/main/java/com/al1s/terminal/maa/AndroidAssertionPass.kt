package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidAssertionPass {
    fun apply(compiled: AndroidCompiledModule, script: JSONObject, entries: Map<Int, String>, materialize: (String) -> String) {
        compiled.stepNodes.forEach { (index, name) ->
            val step = script.getJSONArray("steps").getJSONObject(index)
            val config = step.optJSONObject("post_assertion") ?: return@forEach
            if (config.opt("enabled") != true) return@forEach
            val retries = config.opt("max_retries") ?: 2
            require(retries is Int && retries in 1..20)
            val assertionName = "${name}_Assert"; val retryName = "${name}_AssertRetry"
            val mode = config.optString("recognition_mode", "image")
            require(mode in setOf("image", "text"))
            val source = JSONObject(config.toString()).put("action", if (mode == "image") "wait_image" else "wait_text")
            val assertion = AndroidStepNodes.build(source, index, materialize)
                .put("next", JSONArray().put(entries[index + 1] ?: "End"))
            assertion.getJSONObject("attach").put("maa_project_role", "post-assertion").put("assertion_max_retries", retries)
            compiled.pipeline.put(assertionName, assertion)
            compiled.pipeline.put(retryName, JSONObject().put("recognition", "DirectHit").put("action", "DoNothing")
                .put("next", JSONArray().put(entries.getValue(index))).put("max_hit", retries)
                .put("timeout", AndroidPipelineCompiler.milliseconds(step.optDouble("timeout_seconds", 30.0)))
                .put("rate_limit", AndroidPipelineCompiler.milliseconds(step.optDouble("poll_interval_seconds", 1.0)))
                .put("attach", JSONObject().put("dsl_step_index", index).put("dsl_action", "post_assertion_retry")
                    .put("maa_project_role", "post-assertion-retry")))
            val completion = listOf("${name}_ColorDone", "${name}_MatchDone").firstOrNull { compiled.pipeline.has(it) } ?: name
            compiled.pipeline.getJSONObject(completion).put("next", JSONArray().put(assertionName)).put("on_error", JSONArray().put(retryName))
                .put("timeout", AndroidPipelineCompiler.milliseconds(config.optDouble("timeout_seconds", 3.0)))
                .put("rate_limit", AndroidPipelineCompiler.milliseconds(config.optDouble("poll_interval_seconds", 1.0)))
        }
    }
}
