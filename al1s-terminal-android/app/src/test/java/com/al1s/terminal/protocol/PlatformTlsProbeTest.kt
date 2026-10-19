package com.al1s.terminal.protocol

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlatformTlsProbeTest {
    @Test fun unauthenticatedProbeExplains401WithoutClaimingTerminalAuthentication() {
        val message=PlatformTlsProbe.resultText(401)
        assertTrue(message.contains("已验证"))
        assertTrue(message.contains("未携带认证信息"))
        assertFalse(message.contains("注册失效"))
        assertFalse(message.contains("终端已连接"))
    }

    @Test fun otherStatusesRemainTransportResults() {
        assertTrue(PlatformTlsProbe.resultText(200).endsWith("HTTP 200"))
        assertTrue(PlatformTlsProbe.resultText(403).endsWith("HTTP 403"))
    }
}
