package com.al1s.terminal.protocol

import java.net.URI

object PlatformEndpoint {
    fun normalize(value: String): String {
        val uri = runCatching { URI(value.trim()) }.getOrNull()
        require(
            uri != null && uri.scheme.equals("https", ignoreCase = true) &&
                !uri.host.isNullOrBlank() && uri.rawUserInfo == null &&
                uri.rawQuery == null && uri.rawFragment == null &&
                (uri.path.isNullOrEmpty() || uri.path == "/") &&
                uri.port <= 65535,
        ) { "平台地址必须是证书主机名匹配的 HTTPS 地址" }
        return uri.toASCIIString().trimEnd('/')
    }
}
