package com.al1s.terminal.editor

import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.UUID

class EditorEndpointTest {
    @Test fun fixedChannelUsesTrustedOriginAndBoundIdentity() {
        val url = EditorEndpoint.uplink("https://platform:8443", UUID.randomUUID().toString(),
            UUID.randomUUID().toString(), "video")
        assertTrue(url.startsWith("wss://platform:8443/api/v1/terminal/editor-sessions/"))
    }
    @Test(expected = IllegalArgumentException::class) fun rejectsArbitraryChannel() {
        EditorEndpoint.uplink("https://platform", UUID.randomUUID().toString(),
            UUID.randomUUID().toString(), "shell")
    }
}
