package com.al1s.terminal.maa

import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Native callbacks only invoke explicitly registered behavior; failures return false. */
open class NativeHandlers(private val events: MaaEventProjection, private val port: MaaContextAccess = NativeContextAccess,
    pipeline: JSONObject = JSONObject(), private val device: com.al1s.terminal.device.DeviceAutomationActions? = null,
    evidenceStore:NativeEvidenceStore?=null) {
    open val actionNames = arrayOf("Al1sRepeatedClick", "MaaProjectRandomWait", "Al1sStepBudgetAction", "Al1sFailureSkip",
        "MaaProjectConditionalSkipCapture", "MaaProjectScreenshot", "MaaProjectFeedback", "MaaProjectMatchLoopLimit",
        "MaaProjectFailureRetryProcess", "MaaProjectFailureRetryLimit", "Al1sRecoverySucceeded", "Al1sRetrySucceeded", "Al1sPopupCountClick", "MaaProjectCourseSchedule", "Al1sImageExecution") +
        if (device != null) arrayOf("MaaProjectStart","MaaProjectLaunchActivity") else emptyArray()
    val recognitionNames = arrayOf("Al1sScreenSizeGuard", "Al1sImageMatchStreak", "Al1sStepBudgetRecognition", "Al1sFailureSkipRecognition", "MaaProjectNumericCompare", "MaaProjectColorMarker", "MaaProjectMatchOffset", "Al1sPopupLimitRecognition")
    private val streak = ImageMatchStreak()
    val budget = NativeBudgetHandler(StepBudgetClock(), events)
    private val popup=NativePopupGuard(port,events,budget)
    val failure = NativeFailureState(pipeline)
    private val evidence = evidenceStore ?: NativeEvidenceStore(port)
    private val colorMarkers = ColorMarkerRecognition(port, events)
    private val course = NativeCourseSchedule(port,events,budget)
    init { events.observer = failure::observe }

    @Suppress("UNUSED_PARAMETER")
    fun recognize(context: Long, task: Long, node: String, name: String, parameters: String, image: Long, roi: IntArray): NativeRecognitionResult? =
        runCatching {
            val config = JSONObject(parameters)
            if (port.stopping(context)) return@runCatching null
            if(name=="Al1sPopupLimitRecognition")return@runCatching popup.recognize(context,node,config,image)
            if (name == "MaaProjectColorMarker") return@runCatching colorMarkers.recognize(node,config,image,roi)
            if (name == "Al1sFailureSkipRecognition") return@runCatching if (failure.matches(config.getInt("step_index"), config.getString("phase")))
                NativeRecognitionResult(true, intArrayOf(0, 0, 1, 1), parameters) else null
            val source = config.getString("source")
            if (port.stopping(context)) return@runCatching null
            if (name == "Al1sStepBudgetRecognition") {
                budget.enter(config)
                return@runCatching budget.recognize(config, node) { port.recognize(context, source, image) }
            }
            if (name == "Al1sScreenSizeGuard") {
                val size = port.imageSize(image)
                check(size.contentEquals(intArrayOf(config.getInt("width"), config.getInt("height")))) { "screen_geometry_changed" }
            }
            val result = port.recognize(context, source, image)
            if (name == "MaaProjectMatchOffset") return@runCatching MatchOffsetSelector.select(result,config.optInt("preferred_index",0))
            if (name == "MaaProjectNumericCompare") {
                val detail = runCatching { JSONObject(result.detail) }.getOrDefault(JSONObject())
                val all = detail.optJSONArray("all") ?: org.json.JSONArray()
                val texts = (0 until all.length()).mapNotNull { all.optJSONObject(it)?.optString("text")?.takeIf(String::isNotEmpty) }
                val number = NumericCondition.firstNumber(texts)
                val hit = NumericCondition.matches(number, config.getString("operator"), config.getDouble("value"))
                val diagnostic = JSONObject().put("name", node).put("recognized_value", number ?: JSONObject.NULL)
                    .put("operator", config.getString("operator")).put("threshold", config.getDouble("value")).put("hit", hit)
                events.onEvent("AL1S.NumericCondition", diagnostic.toString())
                val region = config.getJSONArray("region")
                return@runCatching if (hit) NativeRecognitionResult(true, IntArray(4) { region.getInt(it) }, diagnostic.toString()) else null
            }
            if (name == "Al1sImageMatchStreak" && !streak.accept(config.getString("key"),
                    config.getInt("consecutive_match_count"), result.hit)) return@runCatching null
            result.takeIf { it.hit }
        }.getOrElse { fail(node, name, it); null }

    @Suppress("UNUSED_PARAMETER")
    open fun act(context: Long, task: Long, node: String, name: String, parameters: String, recognition: Long, box: IntArray): Boolean =
        runCatching {
            val config = JSONObject(parameters)
            when (name) {
                "MaaProjectCourseSchedule" -> course.run(context,node,config)
                "MaaProjectLaunchActivity" -> {
                    checkNotNull(device).launch(config.getString("package"),config.getString("activity"));true
                }
                "Al1sImageExecution" -> imageExecute(context,config)
                "Al1sPopupCountClick" -> popup.action(context,node,config,box)
                "MaaProjectFailureRetryProcess" -> {
                    val success=port.runTask(context,config.getString("entry"),config.getJSONObject("pipeline").toString())
                    if(success) {
                        val nodes=config.optJSONArray("reset_hit_count_nodes") ?: org.json.JSONArray()
                        for(i in 0 until nodes.length())check(port.clearHitCount(context,nodes.getString(i)))
                    }
                    events.onEvent("AL1S.Recovery.Completed",JSONObject().put("name",node).put("success",success)
                        .put("process_script_name",config.getString("process_script_name")).toString())
                    success
                }
                "MaaProjectFailureRetryLimit" -> {
                    events.onEvent("AL1S.Recovery.Exhausted",JSONObject().put("name",node).put("parameters",config).toString())
                    false
                }
                "Al1sRecoverySucceeded" -> {budget.clock.reset(config.getString("key"));true}
                "Al1sRetrySucceeded" -> port.clearHitCount(context,config.getString("retry_entry"))
                "MaaProjectMatchLoopLimit" -> {
                    events.onEvent("AL1S.MatchLoop.Limit",JSONObject().put("name",node).put("limit",config).toString())
                    false
                }
                "MaaProjectStart" -> {
                    if (port.stopping(context)) false else {
                        val result = checkNotNull(device).start()
                        events.onEvent("AL1S.Device.SessionStarted", JSONObject().put("name", node).put("result", result).toString())
                        true
                    }
                }
                "MaaProjectConditionalSkipCapture", "MaaProjectScreenshot", "MaaProjectFeedback" -> {
                    if (port.stopping(context)) false else {
                        val capture = evidence.capture(context, node)
                        events.onEvent("AL1S.Evidence", JSONObject().put("name", node).put("action", name)
                            .put("parameters", config).put("capture", capture).toString())
                        true
                    }
                }
                "Al1sFailureSkip" -> failure.consume(config.getInt("step_index"), config.getString("phase")).also { consumed ->
                    if (consumed) {
                        budget.clock.finishFailedStep("${config.getString("scope")}:${config.getInt("step_index")}")
                        events.onEvent("AL1S.Step.Skipped", JSONObject().put("name", node).put("step_index", config.getInt("step_index"))
                            .put("target_index", config.getInt("target_index")).put("phase", config.getString("phase")).toString())
                    }
                }
                "Al1sStepBudgetAction" -> {
                    budget.enter(config)
                    budget.action(config, node, { port.stopping(context) }) {
                        port.action(context, config.getString("source"), box, "{}")
                    }
                }
                "Al1sRepeatedClick" -> repeatClick(context, config, box)
                "MaaProjectRandomWait" -> {
                    val lower = config.getDouble("min_seconds"); val upper = config.getDouble("max_seconds")
                    require(lower.isFinite() && upper.isFinite() && lower in 0.0..upper && upper <= 14400)
                    wait(context, lower + kotlin.random.Random.nextDouble() * (upper - lower), Long.MAX_VALUE)
                }
                else -> false
            }
        }.getOrElse { fail(node, name, it); false }

    private fun repeatClick(context: Long, config: JSONObject, initialBox: IntArray): Boolean {
        val count = config.getString("count").toInt()
        require(count in 1..1000000)
        val budget = config.getDouble("budget_seconds")
        require(budget.isFinite() && budget in 0.1..14400.0)
        val deadline = System.nanoTime() + (budget * 1e9).toLong()
        var box = initialBox.copyOf()
        var detail = "{}"
        for (index in 0 until count) {
            if (!ready(context, deadline)) return false
            if (index > 0 && config.optBoolean("recheck_match")) {
                val result = freshMatch(context, config, deadline) ?: return false
                box = result.box; detail = result.detail
            }
            val source = config.getString("source")
            val metadata = JSONObject(port.nodeData(context, source)).optJSONObject("attach")
            val target = if (metadata?.optBoolean("click_match_center") == true) {
                require(box.size == 4 && box[2] > 0 && box[3] > 0)
                intArrayOf(box[0] + (box[2] - 1) / 2, box[1] + (box[3] - 1) / 2, 1, 1)
            } else box
            if (!port.action(context, source, target, detail)) return false
            if (index + 1 < count && !wait(context, config.getInt("interval_ms") / 1000.0, deadline)) return false
        }
        return ready(context, deadline)
    }
    private fun imageExecute(context:Long,config:JSONObject):Boolean {
        val count=config.getInt("count");val interval=config.getInt("interval_ms");val poll=config.getInt("poll_ms")
        require(count in 1..200 && interval in 0..14400000 && poll in 1..14400000)
        val deadline=System.nanoTime()+(budget.clock.remaining()*1e9).toLong()
        repeat(count) { index ->
            var found:NativeRecognitionResult?=null
            while(ready(context,deadline) && found==null) {
                val image=port.capture(context);if(image==0L)return false
                found=try {port.recognize(context,config.getString("source"),image).takeIf {it.hit}} finally {port.destroyImage(image)}
                if(found==null && !wait(context,poll/1000.0,deadline))return false
            }
            val match=found ?: return false
            if(!ready(context,deadline) || !port.action(context,config.getString("source"),match.box,match.detail))return false
            if(index+1<count && !wait(context,interval/1000.0,deadline))return false
        }
        return ready(context,deadline)
    }

    private fun freshMatch(context: Long, config: JSONObject, deadline: Long): NativeRecognitionResult? {
        while (ready(context, deadline)) {
            val image = port.capture(context)
            if (image == 0L) return null
            val result = try {
                config.optJSONArray("size")?.let { expected -> require(port.imageSize(image)
                    .contentEquals(intArrayOf(expected.getInt(0), expected.getInt(1)))) }
                port.recognize(context, config.getString("source"), image)
            } finally { port.destroyImage(image) }
            if (result.hit && ready(context, deadline)) return result
            if (!wait(context, config.optInt("poll_interval_ms", 1000) / 1000.0, deadline)) return null
        }
        return null
    }

    private fun ready(context: Long, deadline: Long) = !budget.fatal &&
        (if(budget.clock.activeKey()!=null)budget.clock.remaining()>0 else System.nanoTime()<deadline) && !port.stopping(context)
    private fun wait(context: Long, seconds: Double, deadline: Long): Boolean {
        require(seconds.isFinite() && seconds in 0.0..14400.0)
        val end = System.nanoTime() + (seconds * 1e9).toLong()
        while (System.nanoTime() < end) {
            if (!ready(context, deadline)) return false
            TimeUnit.NANOSECONDS.sleep(minOf(50_000_000, end - System.nanoTime()).coerceAtLeast(1))
        }
        return ready(context, deadline)
    }
    private fun fail(node: String, name: String, error: Throwable) {
        events.onEvent("AL1S.Handler.Failed", JSONObject().put("name", node).put("handler", name)
            .put("cause", error.javaClass.simpleName).put("message",error.message?.take(256) ?: "").toString())
    }
}
