package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

/** Adapt the platform's immutable execution closure into the same DSL as Linux. */
object AndroidPlanCompiler {
    fun compile(plan: AndroidMaaPlan, imageDirectory: java.io.File): List<Pair<AndroidMaaModule, AndroidCompiledModule>> =
        plan.modules.map { module ->
            val script = script(module, plan)
            val compiled = AndroidPipelineCompiler.compile(script, imageDirectory,plan.resources)
            AndroidStepBudgetPass.apply(compiled, script)
            module to compiled
        }

    fun script(module: AndroidMaaModule, plan: AndroidMaaPlan, stack: Set<String> = emptySet()): JSONObject {
        require(module.definitionKey !in stack && stack.size < 20) { "maa_recovery_cycle" }
        val next = stack + module.definitionKey
        return JSONObject().put("version", 2).put("script_type", module.scriptType).put("target", JSONObject(module.target.toString()))
            .put("script_name", module.scriptName).put("script_version_id", module.scriptVersionId).put("definition_key", module.definitionKey)
            .put("steps", JSONArray(module.steps.map { step(it, plan, next) }))
            .put("global_popups", JSONArray(module.independentRules.map { resolve(it, plan).also { value ->
                (value as JSONObject).put("_definition_key", module.definitionKey)
            } }))
            .put("cleanup_on_finish", module.cleanupOnFinish)
    }

    private fun step(value: JSONObject, plan: AndroidMaaPlan, stack: Set<String>): JSONObject {
        val action = value.getString("action_id")
        val step = resolve(value.getJSONObject("parameters"), plan) as JSONObject
        step.put("action", action)
        val wrappers = value.optJSONArray("wrappers") ?: JSONArray()
        for (index in 0 until wrappers.length()) {
            val wrapper = wrappers.getJSONObject(index)
            val parameters = resolve(wrapper.optJSONObject("parameters") ?: JSONObject(), plan) as JSONObject
            applyWrapper(step, action, wrapper.getString("kind"), parameters, plan, stack)
        }
        return step
    }

    private fun applyWrapper(step: JSONObject, action: String, kind: String, parameters: JSONObject,
        plan: AndroidMaaPlan, stack: Set<String>) {
        when (kind) {
            "conditional_skip" -> step.put("skip_condition", parameters)
            "post_assertion" -> step.put("post_assertion", parameters)
            "wait_after_execution" -> step.put("wait_after_execution_seconds", parameters.get("seconds"))
            "system_key_wait" -> {
                require(action in setOf("back", "home", "task_view"))
                step.put("wait_before_execution_seconds", parameters.get("before_seconds"))
                    .put("wait_after_execution_seconds", parameters.get("after_seconds"))
            }
            "recognize_match_center" -> require(action == "recognize_execute" && step.optString("execution_mode") == "match_center")
            "wait_image_stability" -> {
                require(action == "wait_image")
                step.put("consecutive_match_count", parameters.get("consecutive_match_count"))
            }
            "failure_retry" -> {
                val recovery = requireNotNull(plan.definitions[parameters.getString("recovery_definition_key")]) { "maa_recovery_definition_missing" }
                step.put("failure_retry", JSONObject().put("enabled", true).put("max_retries", parameters.get("max_retries"))
                    .put("process_script_name", recovery.scriptName).put("process_script", script(recovery, plan, stack)))
            }
            else -> throw IllegalArgumentException("maa_wrapper_unregistered")
        }
    }

    private fun resolve(value: Any?, plan: AndroidMaaPlan, depth: Int = 0): Any? {
        require(depth < 50)
        return when (value) {
            is JSONObject -> {
                if (value.has("\$resource")) {
                    val path = requireNotNull(plan.resources[value.getString("\$resource")]) { "maa_resource_missing" }
                    require(path.length() <= 32 * 1024 * 1024)
                    val media = value.optString("media_type", "image/png")
                    require(media in setOf("image/png", "image/jpeg"))
                    "al1s-resource:${value.getString("\$resource")}" 
                } else JSONObject().also { result -> value.keys().forEach { result.put(it, resolve(value.get(it), plan, depth + 1)) } }
            }
            is JSONArray -> JSONArray((0 until value.length()).map { resolve(value.get(it), plan, depth + 1) })
            else -> value
        }
    }
}
