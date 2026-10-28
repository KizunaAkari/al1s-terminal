package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidStepBudgetPass {
    fun apply(compiled: AndroidCompiledModule, script: JSONObject) {
        val wrapped = mutableSetOf<String>()
        compiled.stepNodes.forEach { (index, name) ->
            val step = script.getJSONArray("steps").getJSONObject(index)
            wrap(compiled.pipeline, name, step, index, script.optString("definition_key", "script"))
            wrapped += name
            val cold = "${name}_ColdStop"
            if (compiled.pipeline.has(cold)) { wrap(compiled.pipeline, cold, step, index, script.optString("definition_key", "script")); wrapped += cold }
            for (suffix in listOf("Assert", "AssertRetry", "ColorDone", "ColorLimit", "MatchDone", "MatchLimit", "SwipeAction")) {
                val extra = "${name}_$suffix"
                if (compiled.pipeline.has(extra)) {
                    wrap(compiled.pipeline, extra, step, index, script.optString("definition_key", "script"))
                    wrapped += extra
                }
            }
        }
        compiled.pipeline.keys().asSequence().filter { it.startsWith("Global_") && !it.endsWith("Source") }.toList().forEach { name->
            val node=compiled.pipeline.getJSONObject(name);val index=node.getJSONObject("attach").getInt("dsl_step_index")
            wrap(compiled.pipeline,name,script.getJSONArray("steps").getJSONObject(index),index,script.optString("definition_key","script"))
            wrapped+=name
        }
        compiled.pipeline.keys().forEach { name ->
            val node = compiled.pipeline.getJSONObject(name)
            val next = node.optJSONArray("next") ?: JSONArray()
            if ((0 until next.length()).any { next.optString(it) in wrapped }) node.put("timeout", -1)
        }
    }

    private fun wrap(pipeline: JSONObject, name: String, step: JSONObject, index: Int, scope: String) {
        val node = pipeline.getJSONObject(name)
        val default = maxOf(if(step.optString("action")=="smart_swipe")45.0 else 30.0,
            step.optDouble("seconds", 0.0) + 1, step.optDouble("wait_seconds", 0.0) + 1,step.optDouble("swipe_for_seconds",0.0)+1)
        val budget = step.optDouble("timeout_seconds", default)
        require(budget.isFinite() && budget in 0.1..14400.0)
        val metadata=node.optJSONObject("attach") ?: JSONObject()
        val config = JSONObject().put("key", "$scope:$index").put("budget", budget).put("step_index", index)
            .put("rule", metadata.opt("popup_key") ?: JSONObject.NULL).put("rule_budget", metadata.optDouble("popup_budget",30.0))
            .put("condition", metadata.optBoolean("popup_condition")).put("reset", false)
        val recognition = "${name}_BudgetRecognitionSource"; val action = "${name}_BudgetActionSource"
        pipeline.put(recognition, JSONObject(node.toString()).put("action", "DoNothing").put("next", JSONArray()))
        pipeline.put(action, JSONObject(node.toString()).put("pre_delay", 0).put("post_delay", 0).put("next", JSONArray()))
        node.put("recognition", "Custom").put("custom_recognition", "Al1sStepBudgetRecognition")
            .put("custom_recognition_param", JSONObject(config.toString()).put("source", recognition))
            .put("action", "Custom").put("custom_action", "Al1sStepBudgetAction")
            .put("custom_action_param", JSONObject(config.toString()).put("source", action)
                .put("pre_delay", node.optInt("pre_delay", 0)).put("post_delay", node.optInt("post_delay", 0)))
            .put("pre_delay", 0).put("post_delay", 0).put("repeat", 1)
    }
}
