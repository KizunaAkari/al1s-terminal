package com.al1s.terminal.runtime

import com.al1s.terminal.execution.MaaFailureDiagnostic
import org.junit.Assert.*
import org.junit.Test

class MaaFailureDiagnosticTest {
    @Test fun `formal failure uses the same module and step fields as platform task details`() {
        val detail = MaaFailureDiagnostic.build("免费十连", "version", 3, 1, "recognition", "maa_step_execution_stalled")
        val module = detail.getJSONArray("modules").getJSONObject(0)
        assertEquals(4, module.getInt("module_index"))
        assertEquals("免费十连", module.getString("script_name"))
        assertEquals(2, module.getJSONObject("result").getJSONObject("failed_step").getInt("number"))
        assertEquals("recognition", module.getJSONObject("result").getJSONObject("failure_diagnosis").getString("stage"))
    }
}
