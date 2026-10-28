package com.al1s.terminal.execution

import java.util.UUID

data class AuthorizedAttempt(val attemptId: String, val packageId: String, val packageHash: String,
    val terminalId: String, val deviceId: String, val permitId: String, val permitExpiresAt: Long, val timeoutSeconds: Int,
    val ownerKind:String="formal") {
    fun validate(terminal: String, device: String, now: Long) {
        listOf(attemptId, packageId, terminalId, deviceId, permitId).forEach { UUID.fromString(it) }
        require(terminalId == terminal && deviceId == device && permitExpiresAt > now)
        require(packageHash.matches(Regex("[0-9a-f]{64}")) && timeoutSeconds in 1..86400)
        require(ownerKind in setOf("formal","quick_test"))
    }
    companion object { fun mayStart(status: String, runningOwner: String?) = status == "accepted" && runningOwner == null }
}
