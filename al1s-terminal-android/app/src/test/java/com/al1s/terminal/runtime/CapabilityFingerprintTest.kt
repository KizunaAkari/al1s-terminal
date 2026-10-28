package com.al1s.terminal.runtime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class CapabilityFingerprintTest {
    @Test fun realProviderChangesRepublishWhileFreeSpaceFluctuationsDoNot() {
        val before=JSONObject().put("provider_keys",org.json.JSONArray()).put("details",JSONObject().put("control_ready",false))
        val space=JSONObject(before.toString()).put("storage_available_bytes",999L)
        assertEquals(CapabilityFingerprint.of(before),CapabilityFingerprint.of(space))
        val ready=JSONObject(before.toString()).put("provider_keys",org.json.JSONArray().put("maa"))
        assertNotEquals(CapabilityFingerprint.of(before),CapabilityFingerprint.of(ready))
    }
}
