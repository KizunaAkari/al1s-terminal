package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

internal object AndroidStepNodes {
    fun build(step: JSONObject, index: Int, materialize: (String) -> String): JSONObject {
        val action = step.getString("action")
        val node = JSONObject().put("recognition", "DirectHit").put("action", "DoNothing")
            .put("pre_delay", 0).put("post_delay", 0).put("next", JSONArray())
            .put("attach", JSONObject().put("dsl_step_index", index).put("dsl_action", action).put("maa_project_role", "step"))
        when (action) {
            "course_schedule" -> node.put("action","Custom").put("custom_action","MaaProjectCourseSchedule")
                .put("custom_action_param",JSONObject())
            "start", "screenshot", "feedback", "wait_random" -> {
                val names = mapOf("start" to "MaaProjectStart", "screenshot" to "MaaProjectScreenshot",
                    "feedback" to "MaaProjectFeedback", "wait_random" to "MaaProjectRandomWait")
                if (action == "wait_random") {
                    val lower = step.getDouble("min_seconds"); val upper = step.getDouble("max_seconds")
                    require(lower.isFinite() && upper.isFinite() && lower in 0.0..upper && upper <= 14400)
                }
                val config = JSONObject(step.toString()).apply { remove("action"); remove("template_base64"); remove("click_template_base64") }
                node.put("action", "Custom").put("custom_action", names.getValue(action)).put("custom_action_param", config)
            }
            "cleanup" -> Unit // The attempt owner performs force-stop and Home in its finalizer, including failures.
            "wait", "log" -> if (action == "wait") node.put("post_delay", AndroidPipelineCompiler.milliseconds(step.optDouble("seconds", 1.0)))
            "back", "home", "task_view", "wake", "sleep" -> key(node, action)
            "tap" -> node.put("action", "Click").put("target", JSONArray().put(step.getInt("x")).put(step.getInt("y")))
            "swipe" -> swipe(node, step)
            "wait_image", "wait_click" -> {
                template(node, step, materialize)
                if (action == "wait_click") when (step.optString("click_mode", "fixed")) {
                    "match_center" -> matchCenter(node)
                    "fixed" -> {
                        val click=step.optJSONObject("click") ?: step
                        node.put("action", "Click").put("target", JSONArray().put(click.getInt("x")).put(click.getInt("y")))
                    }
                    else -> error("click_mode_compiler_unavailable")
                }
            }
            "smart_swipe" -> template(node,step,materialize)
            "wait_text", "click_text" -> {
                text(node, step)
                if (action == "click_text") node.put("action", "Click").put("target", true)
            }
            "recognize_execute" -> recognizeExecute(node, step, materialize)
            "launch_app", "close_app" -> {
                node.put("action", if (action == "launch_app") "StartApp" else "StopApp").put("package", step.getString("package"))
                if(action=="launch_app" && step.optString("activity").isNotEmpty()) node.put("action","Custom")
                    .put("custom_action","MaaProjectLaunchActivity").put("custom_action_param",JSONObject()
                        .put("package",step.getString("package")).put("activity",step.getString("activity")))
                if (action == "launch_app") node.put("post_delay", AndroidPipelineCompiler.milliseconds(step.optDouble("wait_seconds", 0.0)))
            }
            else -> throw IllegalArgumentException("action_compiler_unavailable:$action")
        }
        applyWaits(node, step, action)
        val count = step.optInt("execution_count", step.optInt("click_count", 1))
        require(count in 1..200)
        node.put("repeat", count).put("repeat_delay", step.optInt("execution_interval_ms", step.optInt("click_interval_ms", 120)).also { require(it >= 0) })
        return node
    }

    private fun key(node: JSONObject, action: String) {
        val keys = mapOf("back" to 4, "home" to 3, "task_view" to 187, "wake" to 224, "sleep" to 223)
        node.put("action", "ClickKey").put("key", keys.getValue(action))
    }

    private fun template(node: JSONObject, step: JSONObject, materialize: (String) -> String) {
        val file = if (step.has("template")) step.getString("template") else materialize(step.getString("template_base64"))
        require(file.matches(Regex("[A-Za-z0-9_.-]+")) && !file.contains(".."))
        val threshold = step.optDouble("threshold", 0.85)
        require(threshold.isFinite() && threshold in 0.0..1.0)
        node.put("recognition", "TemplateMatch").put("template", file).put("threshold", threshold)
        roi(node, step)
    }

    private fun text(node: JSONObject, step: JSONObject) {
        val text = step.getString("text")
        require(text.isNotBlank() && text.length <= 200)
        val escaped = buildString { text.forEach { if (it in ".^$*+?{}[]\\|()") append('\\'); append(it) } }
        node.put("recognition", "OCR").put("expected", JSONArray().put(escaped)).put("order_by", "Horizontal").put("index", 0)
        roi(node, step)
    }

    private fun roi(node: JSONObject, step: JSONObject) {
        step.optJSONObject("search_region")?.let { region -> node.put("roi", JSONArray()
            .put(region.getInt("x")).put(region.getInt("y")).put(region.getInt("width")).put(region.getInt("height"))) }
    }

    private fun matchCenter(node: JSONObject) {
        node.put("action", "Click").put("target", true)
        node.getJSONObject("attach").put("click_match_center", true)
    }

    private fun swipe(node: JSONObject, step: JSONObject) {
        node.put("action", "Swipe").put("begin", JSONArray().put(step.getInt("x1")).put(step.getInt("y1")))
            .put("end", JSONArray().put(step.getInt("x2")).put(step.getInt("y2")))
            .put("duration", step.optInt("duration_ms", step.optInt("swipe_duration_ms", 300)))
    }

    private fun recognizeExecute(node: JSONObject, step: JSONObject, materialize: (String) -> String) {
        when (step.getString("recognition_mode")) {
            "image" -> template(node, step, materialize)
            "text" -> text(node, step)
            else -> throw IllegalArgumentException("recognition_mode_invalid")
        }
        when (step.getString("execution_mode")) {
            "image_center" -> node.put("action","Custom").put("custom_action","Al1sImageExecution").put("custom_action_param",JSONObject())
            "match_center" -> { require(step.getString("recognition_mode") == "image"); matchCenter(node) }
            "fixed_tap" -> {
                val click = step.getJSONObject("click")
                node.put("action", "Click").put("target", JSONArray().put(click.getInt("x")).put(click.getInt("y")))
            }
            "fixed_swipe" -> swipe(node, step.getJSONObject("swipe"))
            else -> throw IllegalArgumentException("execution_mode_compiler_unavailable")
        }
    }

    private fun applyWaits(node: JSONObject, step: JSONObject, action: String) {
        if (step.has("wait_before_execution_seconds")) {
            require(action in setOf("back", "home", "task_view"))
            node.put("pre_delay", AndroidPipelineCompiler.milliseconds(step.getDouble("wait_before_execution_seconds")))
        }
        if (step.has("wait_after_execution_seconds")) node.put("post_delay", node.getInt("post_delay") +
            AndroidPipelineCompiler.milliseconds(step.getDouble("wait_after_execution_seconds")))
    }
}
