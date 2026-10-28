package com.al1s.terminal.runtime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class QuickEventProjectionTest {
    @Test fun independentRulesHaveNoMainStepAndKeepFrozenDefinitionIdentity() {
        val event=QuickEventProjection.project("AL1S.Rule.Clicked",JSONObject().put("name","Global_2_ForStep_3"),"frozen",null)
        assertNotNull(event);assertNull(event?.step);assertEquals("maa_rule_succeeded:frozen:2",event?.code)
    }
    @Test fun singleStepDebugUsesOriginalStepNumberAndIgnoresNestedSources() {
        val event=QuickEventProjection.project("Node.Action.Succeeded",JSONObject().put("name","Step_000"),"frozen",14)
        assertEquals(14,event?.step)
        assertNull(QuickEventProjection.project("Node.Action.Starting",JSONObject().put("name","Step_000_BudgetActionSource"),"frozen",14))
    }
}
