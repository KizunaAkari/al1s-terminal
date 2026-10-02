package com.al1s.terminal.protocol

import org.json.JSONObject

data class RegistrationResult(
    val terminalId: String,
    val targetDeviceId: String,
    val credential: String,
    val rowVersion: Int,
)

data class TerminalCommand(
    val commandId: String,
    val kind: String,
    val packageId: String?,
    val attemptId: String,
)

data class TaskPackage(
    val packageId: String,
    val attemptId: String,
    val packageHash: String,
    val body: JSONObject,
)

data class OfflinePermit(
    val permitId: String,
    val permitVersion: Int,
    val token: String,
    val expiresAt: String,
)

data class AttemptLease(
    val leaseId: String,
    val leaseVersion: Int,
)

class PlatformException(
    val statusCode: Int,
    val code: String,
    message: String,
    cause: Throwable? = null,
) : RuntimeException(message, cause)
