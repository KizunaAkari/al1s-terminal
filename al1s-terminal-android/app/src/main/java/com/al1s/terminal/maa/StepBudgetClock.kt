package com.al1s.terminal.maa

/** Same monotonic elapsed semantics as the Linux StepClock, including rule pause. */
class StepBudgetClock(private val now: () -> Long = System::nanoTime) {
    private val elapsed = mutableMapOf<String, Long>()
    private var active: String? = null
    private var last = now()
    private var rule: String? = null
    private var ruleStarted = last
    private var budget = 14400.0
    private var ruleBudget = 30.0
    private var lastActivity = last

    @Synchronized fun enter(key: String, seconds: Double, independentRule: String? = null, ruleSeconds: Double = 30.0) {
        require(seconds.isFinite() && seconds > 0 && seconds <= 14400)
        require(ruleSeconds.isFinite() && ruleSeconds > 0 && ruleSeconds <= 14400)
        val current = now()
        if (rule == null) active?.let { elapsed[it] = (elapsed[it] ?: 0) + current - last }
        active = key
        last = current
        lastActivity = current
        budget = seconds
        ruleBudget = ruleSeconds
        if (rule != independentRule) { rule = independentRule; ruleStarted = current }
    }

    @Synchronized fun reset(key: String) {
        elapsed[key] = 0
        active = key
        last = now()
        rule = null
    }

    @Synchronized fun remaining(): Double = if (rule != null) ruleBudget - (now() - ruleStarted) / 1e9
        else budget - ((active?.let { elapsed[it] ?: 0 } ?: 0) + if (active != null) now() - last else 0) / 1e9

    @Synchronized fun stalled(): Boolean = active != null && remaining() <= 0 && now() - lastActivity > 2_000_000_000L
    @Synchronized fun activeKey(): String? = active

    @Synchronized fun finishFailedStep(key: String) {
        if (active != key || rule != null) return
        elapsed[key] = (elapsed[key] ?: 0) + now() - last
        active = null
        last = now()
        lastActivity = last
    }
}
