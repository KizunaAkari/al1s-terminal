package com.al1s.terminal.runtime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class QuickDiagnosticProjectionTest {
    @Test fun independentRuleKeepsNoMainStepEvenInSingleStepDebug() {
        val detail=JSONObject().put("failed_step",JSONObject().put("number",JSONObject.NULL))
            .put("failure_diagnosis",JSONObject().put("stage","independent_rule").put("independent_rule",JSONObject().put("number",2)))
        QuickDiagnosticProjection.restoreStep(detail,14)
        assertTrue(detail.getJSONObject("failed_step").isNull("number"))
    }
    @Test fun mainStepRestoresOriginalSelectedNumber() {
        val detail=JSONObject().put("failed_step",JSONObject().put("number",1))
        QuickDiagnosticProjection.restoreStep(detail,14)
        assertEquals(14,detail.getJSONObject("failed_step").getInt("number"))
        assertEquals(13,detail.getJSONObject("failed_step").getInt("index"))
    }
}
