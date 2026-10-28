package com.al1s.terminal.protocol

import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL
import org.junit.Assert.*
import org.junit.Test

class PlatformBlobClientTest {
    private class Connection(url: URL, private val code: Int, val bytes: ByteArray) : HttpURLConnection(url) {
        var closed = false
        override fun connect() = Unit
        override fun disconnect() { closed = true }
        override fun usingProxy() = false
        override fun getResponseCode() = code
        override fun getInputStream() = ByteArrayInputStream(bytes)
        override fun getHeaderField(name: String?) = when (name) {
            "Content-Range" -> "bytes 2-4/10"
            "X-Content-SHA256" -> "a".repeat(64)
            "Content-Length" -> "10"
            "Content-Type" -> "image/png"
            else -> null
        }
    }
    @Test fun `range stays on authenticated platform origin and always releases connection`() {
        lateinit var connection: Connection
        val client = PlatformBlobClient("https://platform.example") { url -> Connection(url, 206, byteArrayOf(2,3,4)).also { connection = it } }
        assertArrayEquals(byteArrayOf(2,3,4), client.readRange("credential", "b5ee8164-2105-4b64-8f89-c886f42c2f98", 2, 3, 10))
        assertEquals("bytes=2-4", connection.getRequestProperty("Range"))
        assertEquals("Bearer credential", connection.getRequestProperty("Authorization"))
        assertFalse(connection.instanceFollowRedirects)
        assertTrue(connection.closed)
    }
    @Test fun `redirect or mismatched range cannot be treated as authorized resource content`() {
        lateinit var connection: Connection
        val client = PlatformBlobClient("https://platform.example") { url -> Connection(url, 302, ByteArray(0)).also { connection = it } }
        assertThrows(PlatformException::class.java) { client.readRange("credential", "b5ee8164-2105-4b64-8f89-c886f42c2f98", 2, 3, 10) }
        assertTrue(connection.closed)
    }
}
