package com.al1s.terminal.maa

class ImageMatchStreak {
    private var key: String? = null
    private var hits = 0
    fun reset() { key = null; hits = 0 }
    fun accept(step: String, count: Int, hit: Boolean, rule: Boolean = false): Boolean {
        require(count > 0)
        if (rule) { if (hit) reset(); return hit }
        if (key != step) { reset(); key = step }
        if (count == 1) { if (hit) reset(); return hit }
        hits = if (hit) hits + 1 else 0
        if (hits < count) return false
        reset()
        return true
    }
}
