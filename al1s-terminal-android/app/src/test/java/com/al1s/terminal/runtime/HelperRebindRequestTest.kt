package com.al1s.terminal.runtime

import com.al1s.terminal.broker.HelperRebindRequest
import org.junit.Assert.*
import org.junit.Test

class HelperRebindRequestTest {
    @Test fun `only privileged local peer with bounded fresh challenge can rebind`() {
        assertTrue(HelperRebindRequest.accepts(2000, "a".repeat(43), "/data/app/owner/base.apk"))
        assertFalse(HelperRebindRequest.accepts(10123, "a".repeat(43), "/data/app/owner/base.apk"))
        assertFalse(HelperRebindRequest.accepts(2000, "a", "/data/app/owner/base.apk"))
        assertFalse(HelperRebindRequest.accepts(2000, "a".repeat(43), "/sdcard/base.apk"))
        assertFalse(HelperRebindRequest.accepts(2000, "a".repeat(43), "/data/app/owner/../base.apk"))
    }
}
