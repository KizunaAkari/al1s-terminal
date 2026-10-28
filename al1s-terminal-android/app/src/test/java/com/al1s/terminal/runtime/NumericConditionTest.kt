package com.al1s.terminal.runtime

import com.al1s.terminal.maa.NumericCondition
import org.junit.Assert.*
import org.junit.Test

class NumericConditionTest {
    @Test fun `parse the same decimal and thousands formats as Linux`() {
        assertEquals(1234.5, NumericCondition.firstNumber(listOf("数量 1,234.5"))!!, 0.00001)
        assertEquals(12.5, NumericCondition.firstNumber(listOf("12,5"))!!, 0.00001)
        assertEquals(-3.0, NumericCondition.firstNumber(listOf("- 3"))!!, 0.00001)
        assertNull(NumericCondition.firstNumber(listOf("暂无")))
    }
    @Test fun `missing numeric result never becomes a successful skip`() {
        assertFalse(NumericCondition.matches(null, "gt", 7.0))
        assertTrue(NumericCondition.matches(8.0, "gt", 7.0))
        assertFalse(NumericCondition.matches(7.0, "gt", 7.0))
        assertTrue(NumericCondition.matches(6.0, "lt", 7.0))
    }
}
