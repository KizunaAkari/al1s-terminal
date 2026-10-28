package com.al1s.terminal.editor

import com.al1s.terminal.protocol.PlatformEndpoint
import java.util.UUID

object EditorEndpoint {
    val channels = setOf("video", "control", "screenshot", "foreground", "ocr", "app_icon")
    fun uplink(origin: String, session: String, instance: String, channel: String): String {
        val base = PlatformEndpoint.normalize(origin)
        require(channel in channels)
        require(UUID.fromString(session).toString() == session)
        require(UUID.fromString(instance).toString() == instance)
        return base.replaceFirst("https://", "wss://") +
            "/api/v1/terminal/editor-sessions/$session/uplink/$channel?instance_id=$instance"
    }
}
