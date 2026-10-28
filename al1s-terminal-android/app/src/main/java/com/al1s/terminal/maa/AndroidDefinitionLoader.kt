package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files

data class AndroidMaaModule(val definitionKey: String, val scriptVersionId: String, val scriptName: String,
    val scriptType: String, val target: JSONObject, val steps: List<JSONObject>, val independentRules: List<JSONObject>,
    val cleanupOnFinish: Boolean, val waitAfterMs: Int = 0)

data class AndroidMaaPlan(val definitionType: String, val modules: List<AndroidMaaModule>,
    val definitions: Map<String, AndroidMaaModule>, val resources: Map<String, File>)

/** Mirrors the frozen maa-registered-actions v1/v2 contract; no live script reads. */
object AndroidDefinitionLoader {
    private val pipelineActions = setOf("back", "tap", "swipe", "home", "task_view", "launch_app", "recognize_execute", "smart_swipe",
        "wait", "wait_click", "wait_image", "wait_text", "click_text")
    private val registeredActions = setOf("cleanup", "course_schedule", "feedback", "screenshot", "start", "wait_random")
    private val wrappers = setOf("conditional_skip", "failure_skip", "failure_retry", "post_assertion", "post_assertion_text",
        "wait_after_execution", "system_key_wait", "wait_image_stability", "recognize_match_center")
    private val knownHandlers = pipelineActions.map { "maa.pipeline.$it" }.toSet() +
        registeredActions.map { "maa.registered.$it" } + wrappers.map { "maa.wrapper.$it" }

    fun load(source: JSONObject, resources: Map<String, File>): AndroidMaaPlan {
        val manifest = JSONObject(source.toString())
        require(manifest.opt("schema_version") == 1) { "maa_definition_schema_unsupported" }
        val type = manifest.getString("definition_type")
        require(type in setOf("quick_test", "script", "strategy")) { "maa_definition_type_unsupported" }
        val raw = manifest.getJSONObject("definitions")
        require(raw.length() in 1..1000) { "maa_definition_closure_missing" }
        val definitions = raw.keys().asSequence().associateWith { key -> module(key, raw.getJSONObject(key), raw, resources) }
        val modules = entries(manifest, type).map { (key, delay) ->
            requireNotNull(definitions[key]) { "maa_definition_closure_missing" }.copy(waitAfterMs = delay)
        }
        validateRecovery(definitions)
        return AndroidMaaPlan(type, modules, definitions, resources.toMap())
    }

    private fun entries(manifest: JSONObject, type: String): List<Pair<String, Int>> {
        if (type != "strategy") return listOf(text(manifest, "entry_definition_key") to 0)
        val array = manifest.getJSONArray("modules")
        require(array.length() in 1..1000) { "maa_strategy_empty" }
        val positions = mutableSetOf<Int>()
        return (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            val position = integer(item.get("position"), 0, Int.MAX_VALUE)
            require(positions.add(position)) { "maa_strategy_module_invalid" }
            Triple(position, text(item, "definition_key"), integer(item.opt("wait_after_ms") ?: 0, 0, 3600000))
        }.sortedBy { it.first }.map { it.second to it.third }
    }

    private fun module(key: String, raw: JSONObject, definitions: JSONObject, resources: Map<String, File>): AndroidMaaModule {
        require(raw.opt("schema_version") == 1 && raw.optString("compiler_version") in
            setOf("maa-registered-actions-v1", "maa-registered-actions-v2")) { "maa_compiler_version_unsupported" }
        val steps = objects(raw.getJSONArray("steps"), 1000)
        steps.forEachIndexed { index, step ->
            require(step.opt("step_index") == index + 1 && step.optString("handler_id") in knownHandlers) { "maa_step_invalid" }
            val action = text(step, "action_id")
            val expected = if (action in registeredActions) "maa.registered.$action" else "maa.pipeline.$action"
            require(step.getString("handler_id") == expected) { "maa_handler_action_mismatch" }
            checkResources(step.getJSONObject("parameters"), resources)
            objects(step.optJSONArray("wrappers") ?: JSONArray(), 50).forEach { wrapper ->
                val parameters = wrapper.optJSONObject("parameters") ?: JSONObject()
                val kind = wrapper.optString("kind")
                val handlerKind = when {
                    kind == "conditional_skip" && parameters.optString("mode") in setOf("recognition_failure", "execution_failure") -> "failure_skip"
                    kind == "post_assertion" && parameters.optString("recognition_mode") == "text" -> "post_assertion_text"
                    else -> kind
                }
                require(wrapper.optString("handler_id") in knownHandlers &&
                    kind in wrappers && wrapper.getString("handler_id") == "maa.wrapper.$handlerKind") { "maa_wrapper_unregistered" }
                checkResources(parameters, resources)
                if (parameters.has("recovery_definition_key"))
                    require(definitions.has(parameters.getString("recovery_definition_key"))) { "maa_recovery_definition_missing" }
            }
        }
        val rules = objects(raw.optJSONArray("independent_rules") ?: JSONArray(), 50)
        rules.forEach { checkResources(it, resources) }
        return AndroidMaaModule(key, text(raw, "script_version_id"), text(raw, "script_name"),
            raw.optString("script_type", "standard"), raw.optJSONObject("target") ?: JSONObject(), steps, rules,
            raw.opt("cleanup_on_finish") == true)
    }

    private fun validateRecovery(definitions: Map<String, AndroidMaaModule>) {
        fun visit(key: String, stack: Set<String>) {
            require(key !in stack && stack.size < 20) { "maa_recovery_cycle" }
            definitions.getValue(key).steps.forEach { step ->
                objects(step.optJSONArray("wrappers") ?: JSONArray(), 50).forEach { wrapper ->
                    wrapper.optJSONObject("parameters")?.optString("recovery_definition_key")?.takeIf { it.isNotEmpty() }
                        ?.let { visit(it, stack + key) }
                }
            }
        }
        definitions.keys.forEach { visit(it, emptySet()) }
    }

    private fun checkResources(value: Any?, resources: Map<String, File>, depth: Int = 0) {
        require(depth < 50) { "maa_resource_nesting_limit" }
        when (value) {
            is JSONObject -> {
                if (value.has("\$resource")) {
                    val file = requireNotNull(resources[value.getString("\$resource")]) { "maa_resource_missing" }
                    require(file.isFile && !Files.isSymbolicLink(file.toPath())) { "maa_resource_invalid" }
                }
                value.keys().forEach { checkResources(value.get(it), resources, depth + 1) }
            }
            is JSONArray -> (0 until value.length()).forEach { checkResources(value.get(it), resources, depth + 1) }
        }
    }

    private fun objects(value: JSONArray, limit: Int): List<JSONObject> {
        require(value.length() <= limit)
        return (0 until value.length()).map { value.getJSONObject(it) }
    }
    private fun text(value: JSONObject, key: String) = value.getString(key).also { require(it.isNotBlank()) }
    private fun integer(value: Any, lower: Int, upper: Int): Int {
        require(value is Int && value in lower..upper) { "maa_integer_invalid" }
        return value
    }
}
