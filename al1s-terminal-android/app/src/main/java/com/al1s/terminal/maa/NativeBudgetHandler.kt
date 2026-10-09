package com.al1s.terminal.maa

import org.json.JSONObject
import java.util.concurrent.TimeUnit

class NativeBudgetHandler(val clock: StepBudgetClock, private val events: MaaEventProjection) {
    @Volatile var fatal = false
        private set

    fun enter(config: JSONObject) {
        if (config.optBoolean("reset")) clock.reset(config.getString("key"))
        clock.enter(config.getString("key"), config.getDouble("budget"), config.opt("rule") as? String,
            config.optDouble("rule_budget", 30.0))
    }

    fun recognize(config: JSONObject, node: String, delegate: () -> NativeRecognitionResult): NativeRecognitionResult? {
        if (fatal) return null
        if (!available(config, node)) return timeoutResult(config)
        val result = delegate()
        if (config.opt("rule") is String && config.optBoolean("condition") && !result.hit)
            clock.enter(config.getString("key"), config.getDouble("budget"))
        if (!available(config, node)) return timeoutResult(config)
        return result.takeIf { it.hit }
    }

    fun action(config: JSONObject, node: String, stopping: () -> Boolean, delegate: () -> Boolean): Boolean {
        if (fatal || stopping() || !available(config, node)) return false
        if (!delay(config.optDouble("pre_delay", 0.0), config, node, stopping)) return false
        val result = delegate()
        return result && !stopping() && available(config, node) &&
            delay(config.optDouble("post_delay", 0.0), config, node, stopping)
    }

    fun markFatal() { fatal = true }

    private fun available(config: JSONObject, node: String): Boolean {
        if (clock.remaining() > 0) return true
        val rule = config.opt("rule") as? String
        if (rule != null) fatal = true
        events.onEvent(if (rule == null) "AL1S.Step.Timeout" else "AL1S.Rule.Timeout", JSONObject()
            .put("name", node).put("step_index", config.getInt("step_index")).put("rule", rule ?: JSONObject.NULL)
            .put("budget_seconds", if (rule == null) config.getDouble("budget") else config.getDouble("rule_budget")).toString())
        return false
    }

    private fun timeoutResult(config: JSONObject): NativeRecognitionResult? = if (config.opt("rule") is String) null
        else NativeRecognitionResult(true, intArrayOf(0, 0, 1, 1), "{\"step_timeout\":true}")

    private fun delay(milliseconds: Double, config: JSONObject, node: String, stopping: () -> Boolean): Boolean {
        require(milliseconds.isFinite() && milliseconds in 0.0..14400000.0)
        val deadline = System.nanoTime() + (milliseconds * 1_000_000).toLong()
        while (System.nanoTime() < deadline) {
            if (fatal || stopping() || !available(config, node)) return false
            TimeUnit.NANOSECONDS.sleep(minOf(50_000_000, deadline - System.nanoTime()).coerceAtLeast(1))
        }
        return !stopping() && available(config, node)
    }
}
