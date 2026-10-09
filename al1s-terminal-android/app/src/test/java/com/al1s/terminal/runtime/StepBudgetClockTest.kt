package com.al1s.terminal.runtime

import com.al1s.terminal.maa.StepBudgetClock
import com.al1s.terminal.maa.ImageMatchStreak
import org.junit.Assert.*
import org.junit.Test

class StepBudgetClockTest {
    @Test fun `independent rule handling consumes its own budget and pauses main step`() {
        var time = 0L
        val clock = StepBudgetClock { time }
        clock.enter("step-2", 20.0)
        time = 5_000_000_000
        clock.enter("step-2", 20.0, "rule-0", 3.0)
        time = 7_000_000_000
        assertEquals(1.0, clock.remaining(), 0.0001)
        clock.enter("step-2", 20.0)
        assertEquals(15.0, clock.remaining(), 0.0001)
        time = 22_000_000_000
        assertEquals(0.0, clock.remaining(), 0.0001)
    }
    @Test fun `streak requires consecutive samples and matched rule clears main streak`() {
        val streak = ImageMatchStreak()
        assertFalse(streak.accept("main", 3, true))
        assertFalse(streak.accept("main", 3, false))
        assertFalse(streak.accept("main", 3, true))
        assertFalse(streak.accept("main", 3, true))
        assertTrue(streak.accept("main", 3, true))
        assertFalse(streak.accept("main", 3, true))
        assertTrue(streak.accept("main", 1, true, rule = true))
        assertFalse(streak.accept("main", 3, true))
    }
    @Test fun `missed single-hit skip candidate does not erase main streak`() {
        val streak = ImageMatchStreak()
        assertFalse(streak.accept("main", 2, true))
        assertFalse(streak.accept("main", 1, false))
        assertTrue(streak.accept("main", 2, true))
    }
}
