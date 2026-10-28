package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidFailureSkipPass {
    fun apply(compiled: AndroidCompiledModule, script: JSONObject, entries: Map<Int, String>) {
        val guards = mutableListOf<String>()
        val steps = script.getJSONArray("steps")
        for (index in 0 until steps.length()) {
            val step = steps.getJSONObject(index)
            val condition = step.optJSONObject("skip_condition") ?: continue
            if (condition.opt("enabled") != true) continue
            val mode = condition.optString("mode")
            if (mode !in setOf("recognition_failure", "execution_failure")) continue
            require(step.getString("action") != "start")
            val raw = condition.opt("skip_to_step_index") ?: (index + 2)
            val maximum = steps.length() + if (!condition.has("skip_to_step_index") && index == steps.length() - 1) 1 else 0
            require(raw is Int && raw > index + 1 && raw <= maximum) {
                "failure_skip_target_invalid"
            }
            val name = "${compiled.stepNodes.getValue(index)}_SkipIfFailure"
            val config = JSONObject().put("step_index", index).put("target_index", raw - 1)
                .put("phase", mode.removeSuffix("_failure")).put("scope", script.optString("definition_key", "script"))
            compiled.pipeline.put(name, JSONObject().put("recognition", "Custom").put("custom_recognition", "Al1sFailureSkipRecognition")
                .put("custom_recognition_param", JSONObject(config.toString())).put("action", "Custom").put("custom_action", "Al1sFailureSkip")
                .put("custom_action_param", config).put("next", JSONArray().put(entries[raw - 1] ?: "End"))
                .put("timeout", -1).put("rate_limit", 100).put("attach", JSONObject().put("dsl_step_index", index)
                    .put("dsl_action", "failure_skip").put("maa_project_role", "failure-skip")))
            guards += name
        }
        if (guards.isEmpty()) return
        val reject = "FailureSkipReject"
        compiled.pipeline.put(reject, JSONObject().put("recognition", "DirectHit").put("action", "Custom")
            .put("custom_action", "Al1sFailureSkip").put("custom_action_param", JSONObject().put("step_index", -1).put("phase", "none"))
            .put("next", JSONArray()))
        compiled.pipeline.keys().forEach { name ->
            if (name in guards || name == reject || name.endsWith("Source")) return@forEach
            val node = compiled.pipeline.getJSONObject(name)
            val current = node.optJSONArray("on_error") ?: JSONArray()
            guards.forEach { current.put(it) }
            current.put(reject)
            node.put("on_error", current)
        }
    }
}
