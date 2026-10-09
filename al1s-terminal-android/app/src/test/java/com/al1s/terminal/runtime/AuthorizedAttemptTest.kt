package com.al1s.terminal.runtime

import com.al1s.terminal.execution.AuthorizedAttempt
import org.junit.Assert.*
import org.junit.Test

class AuthorizedAttemptTest {
    private fun request() = AuthorizedAttempt("b5ee8164-2105-4b64-8f89-c886f42c2f98",
        "b5ee8164-2105-4b64-8f89-c886f42c2f99", "a".repeat(64), "b5ee8164-2105-4b64-8f89-c886f42c2f97",
        "b5ee8164-2105-4b64-8f89-c886f42c2f96", "b5ee8164-2105-4b64-8f89-c886f42c2f95", 1100, 20)
    @Test fun `start is bound to package device and terminal and permit cannot expire before launch`() {
        val request = request()
        request.validate(request.terminalId, request.deviceId, 1000)
        assertThrows(IllegalArgumentException::class.java) { request.validate(request.terminalId, "different", 1000) }
        assertThrows(IllegalArgumentException::class.java) { request.validate(request.terminalId, request.deviceId, 1100) }
        assertThrows(IllegalArgumentException::class.java) { request.copy(packageHash="unsafe").validate(request.terminalId, request.deviceId, 1000) }
    }
    @Test fun `completed or already running attempt cannot be started again`() {
        assertTrue(AuthorizedAttempt.mayStart("accepted", null))
        assertFalse(AuthorizedAttempt.mayStart("running", "instance"))
        assertFalse(AuthorizedAttempt.mayStart("completed", null))
        assertFalse(AuthorizedAttempt.mayStart("interrupted", null))
    }
}
