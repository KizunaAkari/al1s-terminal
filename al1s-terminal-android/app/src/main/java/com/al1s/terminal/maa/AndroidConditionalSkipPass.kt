package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidConditionalSkipPass {
    fun apply(compiled: AndroidCompiledModule, script: JSONObject, entries: Map<Int, String>, materialize: (String) -> String) {
        val guards = mutableMapOf<String, String>()
        compiled.stepNodes.forEach { (index, name) ->
            val condition = script.getJSONArray("steps").getJSONObject(index).optJSONObject("skip_condition") ?: return@forEach
            if (condition.opt("enabled") != true || condition.optString("mode") in setOf("recognition_failure", "execution_failure")) return@forEach
            val mode = condition.optString("mode", "numeric")
            require(mode in setOf("image", "numeric"))
            val target = condition.opt("skip_to_step_index") ?: (index + 2)
            require(target is Int && target > index + 1 && target <= compiled.stepNodes.size +
                if (!condition.has("skip_to_step_index") && index == compiled.stepNodes.size - 1) 1 else 0)
            val guardName = "${name}_SkipIfCondition"
            val guard = if (mode == "image") {
                val image = JSONObject(condition.toString()).put("action", "wait_image")
                if (!image.has("template")) image.put("template_base64", image.getString("preview_base64"))
                AndroidStepNodes.build(image, index, materialize)
            } else numeric(compiled, guardName, condition, index)
            guard.put("action", "Custom").put("custom_action", "MaaProjectConditionalSkipCapture")
                .put("custom_action_param", JSONObject().put("step_index", index).put("mode", mode))
                .put("next", JSONArray().put(entries[target - 1] ?: "End")).put("post_delay", 0)
            guard.put("attach", JSONObject().put("dsl_step_index", index).put("dsl_action", "skip_condition")
                .put("maa_project_role", "skip-condition-$mode"))
            compiled.pipeline.put(guardName, guard)
            guards[entries.getValue(index)] = guardName
        }
        compiled.pipeline.keys().forEach { name ->
            val node = compiled.pipeline.getJSONObject(name)
            if (name in guards.values || name.endsWith("Source")) return@forEach
            val next = node.optJSONArray("next") ?: return@forEach
            val replacement = JSONArray()
            for (index in 0 until next.length()) {
                val value = next.get(index)
                if (value is String) guards[value]?.let { replacement.put(it) }
                replacement.put(value)
            }
            node.put("next", replacement)
        }
    }

    private fun numeric(compiled: AndroidCompiledModule, name: String, condition: JSONObject, index: Int): JSONObject {
        val operator = condition.getString("operator"); val threshold = condition.getDouble("value")
        require(operator in setOf("gt", "lt") && threshold.isFinite())
        val region = condition.getJSONObject("region")
        val roi = JSONArray().put(region.getInt("x")).put(region.getInt("y")).put(region.getInt("width")).put(region.getInt("height"))
        val source = "${name}_NumericSource"
        compiled.pipeline.put(source, JSONObject().put("recognition", "OCR").put("expected", JSONArray()).put("roi", roi)
            .put("action", "DoNothing").put("next", JSONArray()))
        return JSONObject().put("recognition", "Custom").put("custom_recognition", "MaaProjectNumericCompare")
            .put("custom_recognition_param", JSONObject().put("source", source).put("operator", operator).put("value", threshold)
                .put("region", roi).put("step_index", index)).put("roi", roi).put("pre_delay", 0)
    }
}
