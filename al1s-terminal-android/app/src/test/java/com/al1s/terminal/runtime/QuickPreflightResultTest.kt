package com.al1s.terminal.runtime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class QuickPreflightResultTest {
    @Test fun unsupportedDefinitionProducesAReportWithoutInventingAHelperAttempt() {
        val result=QuickPreflightResult.failure(IllegalArgumentException("maa_wrapper_unregistered"))
        assertFalse(result.getBoolean("passed"))
        assertTrue(QuickPreflightResult.isLocalFailure(result))
        assertFalse(QuickPreflightResult.isLocalFailure(JSONObject().put("passed",false)
            .put("error_code","maa_pipeline_failed").put("diagnostic",JSONObject())))
    }
}
