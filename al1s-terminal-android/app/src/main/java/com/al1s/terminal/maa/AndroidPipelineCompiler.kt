package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import kotlin.math.round

data class AndroidCompiledModule(val entry: String, val pipeline: JSONObject, val stepNodes: Map<Int, String>)

/** Portable DSL pass. Unsupported behavior is rejected before an execution is admitted. */
object AndroidPipelineCompiler {
    fun compile(script: JSONObject, imageDirectory: File? = null, resourceFiles:Map<String,File> = emptyMap()): AndroidCompiledModule {
        val steps = script.getJSONArray("steps")
        require(steps.length() <= 1000)
        val pipeline = JSONObject()
        val names = mutableMapOf<Int, String>()
        val entries = mutableMapOf<Int, String>()
        for (index in 0 until steps.length()) {
            val step = steps.getJSONObject(index)
            val name = "Step_%03d".format(index)
            names[index] = name
            val node = if (step.getString("action") == "wait_click" && step.optString("click_mode") == "color_marker")
                AndroidColorLoopPass.target(step, index) else if (step.getString("action") == "wait_click" && step.optString("click_mode") == "match_offset")
                AndroidMatchLoopPass.target(step,index) { materialize(it,imageDirectory,resourceFiles) }
                else AndroidStepNodes.build(step, index) { template -> materialize(template, imageDirectory,resourceFiles) }
            pipeline.put(name, node)
            entries[index] = name
            if (step.getString("action") == "launch_app" && step.opt("force_stop_before_launch") != false) {
                val stop = JSONObject(node.toString()).put("action", "StopApp").put("post_delay", 0)
                    .put("next", JSONArray().put(name)).put("timeout", 2000).put("rate_limit", 100)
                pipeline.put("${name}_ColdStop", stop)
                entries[index] = "${name}_ColdStop"
            }
            stableWait(pipeline, name, node, step)
            repeatAction(pipeline, name, node, step, index, script)
        }
        val end = JSONObject().put("recognition", "DirectHit").put("action", "DoNothing").put("next", JSONArray())
            .put("pre_delay", 0).put("post_delay", 0).put("attach", JSONObject().put("maa_project_role", "end"))
        pipeline.put("End", end)
        val root = JSONObject(end.toString()).put("next", JSONArray().put(entries[0] ?: "End"))
            .put("attach", JSONObject().put("maa_project_role", "root"))
        if (steps.length() > 0) lookupTiming(root, steps.getJSONObject(0))
        pipeline.put("Root", root)
        names.forEach { (index, name) ->
            val node = pipeline.getJSONObject(name)
            node.put("next", JSONArray().put(entries[index + 1] ?: "End"))
            if (index + 1 < steps.length()) lookupTiming(node, steps.getJSONObject(index + 1))
        }
        return AndroidCompiledModule("Root", pipeline, names.toMap()).also {
            AndroidCoursePass.apply(it,script,imageDirectory) { materialize(it,imageDirectory,resourceFiles) }
            AndroidImageExecutionPass.apply(it,script) { materialize(it,imageDirectory,resourceFiles) }
            AndroidColorLoopPass.apply(it, script)
            AndroidMatchLoopPass.apply(it,script) { materialize(it,imageDirectory,resourceFiles) }
            AndroidSmartSwipePass.apply(it,script)
            it.stepNodes.forEach { (index,name)->
                val extra="${name}_SwipeAction"
                if(it.pipeline.has(extra))repeatAction(it.pipeline,extra,it.pipeline.getJSONObject(extra),
                    script.getJSONArray("steps").getJSONObject(index),index,script)
            }
            AndroidAssertionPass.apply(it, script, entries) { template -> materialize(template, imageDirectory,resourceFiles) }
            AndroidConditionalSkipPass.apply(it, script, entries) { template -> materialize(template, imageDirectory,resourceFiles) }
            AndroidRecoveryPass.apply(it,script,imageDirectory,entries,resourceFiles)
            AndroidIndependentRulesPass.apply(it,script) { materialize(it,imageDirectory,resourceFiles) }
            AndroidFailureSkipPass.apply(it, script, entries)
            it.pipeline.keys().asSequence().toList().forEach { key ->
                val node = it.pipeline.getJSONObject(key)
                if (!key.endsWith("Source") && node.optJSONObject("attach")?.has("dsl_step_index") == true)
                    frameGuard(it.pipeline, key, node, screenSize(script))
            }
        }
    }

    private fun lookupTiming(node: JSONObject, next: JSONObject) {
        node.put("timeout", milliseconds(next.optDouble("timeout_seconds", 30.0)))
        node.put("rate_limit", milliseconds(next.optDouble("poll_interval_seconds", 1.0)).coerceAtLeast(1))
    }

    private fun repeatAction(pipeline: JSONObject, name: String, node: JSONObject, step: JSONObject, index: Int, script: JSONObject) {
        val count = node.optInt("repeat", 1)
        if (node.getString("action") !in setOf("Click", "Swipe") ||
            count <= 1 && !node.getJSONObject("attach").optBoolean("click_match_center")) return
        val source = "${name}_RepeatSource"
        pipeline.put(source, JSONObject(node.toString()).put("repeat", 1).put("repeat_delay", 0)
            .put("pre_delay", 0).put("post_delay", 0).put("next", JSONArray()))
        val config = JSONObject().put("source", source).put("count", count.toString())
            .put("interval_ms", node.optInt("repeat_delay", 0)).put("step_index", index)
            .put("budget_seconds", step.optDouble("timeout_seconds", maxOf(30.0,step.optDouble("swipe_for_seconds",0.0)+1)))
            .put("size", screenSize(script)?.let { JSONArray().put(it.first).put(it.second) } ?: JSONObject.NULL)
        if (node.getJSONObject("attach").optBoolean("click_match_center")) config.put("recheck_match", true)
            .put("poll_interval_ms", milliseconds(step.optDouble("poll_interval_seconds", 1.0)).coerceIn(50, 10000))
        node.put("action", "Custom").put("custom_action", "Al1sRepeatedClick").put("custom_action_param", config)
            .put("repeat", 1).put("repeat_delay", 0)
    }

    private fun stableWait(pipeline: JSONObject, name: String, node: JSONObject, step: JSONObject) {
        if (step.getString("action") != "wait_image") return
        val count = step.opt("consecutive_match_count") ?: 1
        require(count is Int && count >= 1)
        if (count == 1) return
        val source = "${name}_StreakSource"
        pipeline.put(source, JSONObject(node.toString()).put("action", "DoNothing").put("next", JSONArray()))
        node.put("recognition", "Custom").put("custom_recognition", "Al1sImageMatchStreak")
            .put("custom_recognition_param", JSONObject().put("source", source).put("key", name).put("consecutive_match_count", count))
    }

    private fun frameGuard(pipeline: JSONObject, name: String, node: JSONObject, size: Pair<Int, Int>?) {
        if (size == null || node.getString("action") == "StartApp") return
        val source = "${name}_ScreenSource"
        pipeline.put(source, JSONObject(node.toString()).put("action", "DoNothing").put("next", JSONArray()))
        node.put("recognition", "Custom").put("custom_recognition", "Al1sScreenSizeGuard")
            .put("custom_recognition_param", JSONObject().put("source", source).put("width", size.first).put("height", size.second))
    }

    private fun screenSize(script: JSONObject): Pair<Int, Int>? {
        val size = script.optJSONObject("target")?.optJSONObject("screen_size") ?: return null
        val width = size.get("width"); val height = size.get("height")
        require(width is Int && height is Int && width in 1..8192 && height in 1..8192 && width.toLong() * height <= 16777216)
        return width to height
    }

    private fun materialize(base64: String, imageDirectory: File?, resourceFiles:Map<String,File>): String {
        val root = requireNotNull(imageDirectory) { "template_image_directory_required" }
        if(base64.startsWith("al1s-resource:")) {
            val source=checkNotNull(resourceFiles[base64.removePrefix("al1s-resource:")]) {"template_resource_missing"}
            return ResourceTemplateFile.materialize(source,root)
        }
        require(base64.length <= 48 * 1024 * 1024)
        val encoded=if(base64.startsWith("data:")) {
            require(base64.startsWith("data:image/png;base64,") || base64.startsWith("data:image/jpeg;base64,"))
            base64.substringAfter("base64,")
        } else base64
        val bytes = java.util.Base64.getDecoder().decode(encoded)
        require(bytes.size in 8..(32 * 1024 * 1024))
        val hash = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        val name = "img_$hash.png"
        check(root.isDirectory || root.mkdirs())
        File(root, name).outputStream().use { it.write(bytes) }
        return name
    }

    internal fun milliseconds(seconds: Double): Int {
        require(seconds.isFinite() && seconds in 0.0..14400.0) { "delay_or_budget_invalid" }
        return round(seconds * 1000).toInt()
    }
}
