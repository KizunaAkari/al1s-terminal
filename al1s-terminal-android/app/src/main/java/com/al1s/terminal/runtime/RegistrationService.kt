package com.al1s.terminal.runtime

import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.protocol.PlatformEndpoint
import com.al1s.terminal.security.RegistrationIdentityStore

class RegistrationService(
    private val identityStore: RegistrationIdentityStore,
    private val client: (String) -> PlatformClient = { PlatformClient(it) },
) {
    fun register(baseUrl: String, registrationCode: String, displayName: String): String {
        val secureBaseUrl = PlatformEndpoint.normalize(baseUrl)
        require(registrationCode.isNotBlank()) { "请输入一次性注册码" }
        require(displayName.isNotBlank()) { "请输入终端名称" }
        val result = client(secureBaseUrl).register(
            registrationCode.trim(),
            identityStore.installationId(),
            displayName.trim(),
            PlatformClient.AGENT_VERSION,
        )
        identityStore.saveRegistration(
            secureBaseUrl,
            displayName.trim(),
            result.terminalId,
            result.targetDeviceId,
            result.credential,
            result.rowVersion,
        )
        return result.targetDeviceId
    }
}
