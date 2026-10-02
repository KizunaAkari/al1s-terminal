package com.al1s.terminal.protocol

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class PlatformClientTest {
    @Test
    fun registrationUsesTheSharedAndroidTerminalContract() {
        val server = ServerSocket(0, 1)
        val executor = Executors.newSingleThreadExecutor()
        val requestHandled = executor.submit {
            val socket = server.accept()
            val input = socket.getInputStream().bufferedReader(Charsets.UTF_8)
            assertEquals("POST /api/v1/terminals/register HTTP/1.1", input.readLine())
            val headers = generateSequence { input.readLine() }
                .takeWhile { it.isNotEmpty() }
                .associate { line ->
                    val separator = line.indexOf(':')
                    line.substring(0, separator).lowercase() to line.substring(separator + 1).trim()
                }
            val contentLength = headers.getValue("content-length").toInt()
            val body = CharArray(contentLength)
            var offset = 0
            while (offset < contentLength) {
                val count = input.read(body, offset, contentLength - offset)
                check(count >= 0) { "Unexpected end of registration request" }
                offset += count
            }
            val request = JSONObject(String(body))
            assertEquals("android", request.getString("terminal_type"))
            val response = JSONObject()
                .put(
                    "terminal",
                    JSONObject()
                        .put("terminal_id", "f3c6fc3c-8100-4838-8ae3-1d8c4d99f56d")
                        .put("row_version", 1),
                )
                .put("credential", "terminal-credential")
                .put(
                    "target_device",
                    JSONObject().put(
                        "device_id",
                        "510504cb-ae11-4340-87a2-f06c473e8ec5",
                    ),
                )
                .toString()
                .toByteArray(Charsets.UTF_8)
            socket.getOutputStream().buffered().use { output ->
                output.write("HTTP/1.1 201 Created\r\n".toByteArray())
                output.write("Content-Type: application/json\r\n".toByteArray())
                output.write("Content-Length: ${response.size}\r\n".toByteArray())
                output.write("Connection: close\r\n\r\n".toByteArray())
                output.write(response)
            }
            socket.close()
        }
        try {
            val result = PlatformClient("https://platform.example", connectionFactory = {
                URL("http://127.0.0.1:${server.localPort}${it.path}").openConnection() as HttpURLConnection
            }).register(
                "registration-code",
                "9e89245c-ed34-466e-bf36-9db07d1d7380",
                "Xiaomi root demo",
                PlatformClient.AGENT_VERSION,
            )

            assertEquals("f3c6fc3c-8100-4838-8ae3-1d8c4d99f56d", result.terminalId)
            assertEquals("510504cb-ae11-4340-87a2-f06c473e8ec5", result.targetDeviceId)
            assertEquals("terminal-credential", result.credential)
            requestHandled.get(5, TimeUnit.SECONDS)
        } finally {
            server.close()
            executor.shutdownNow()
        }
    }

    @Test
    fun rejectsCleartextAndInvalidEndpointsBeforeSendingCredentials() {
        for (value in listOf(
            "http://platform.example", "https://user:secret@platform.example",
            "https://platform.example/other", "https://platform.example?redirect=http://other",
            "https://", "ftp://platform.example",
        )) {
            try {
                PlatformClient(value)
                fail("Accepted invalid platform endpoint: $value")
            } catch (_: IllegalArgumentException) {
                // Expected before any connection is opened.
            }
        }
        assertEquals("https://platform.example:8443", PlatformEndpoint.normalize(" https://platform.example:8443/ "))
    }

    @Test
    fun connectionAndPostWriteFailuresAreTransportErrors() {
        val connectionFailure = PlatformClient("https://platform.example") {
            throw IOException("connection failed")
        }
        assertTransportFailure { connectionFailure.heartbeat("secret", "terminal", 1) }

        val writeConnection = FailingConnection(URL("https://platform.example"), failWrite = true)
        val writeFailure = PlatformClient("https://platform.example") { writeConnection }
        assertTransportFailure { writeFailure.heartbeat("secret", "terminal", 1) }
        assertTrue(writeConnection.disconnected)
    }

    @Test
    fun responseReadFailureDisconnectsAndRedirectIsNotFollowed() {
        val readConnection = FailingConnection(URL("https://platform.example"), failRead = true)
        val readFailure = PlatformClient("https://platform.example") { readConnection }
        assertTransportFailure { readFailure.heartbeat("secret", "terminal", 1) }
        assertTrue(readConnection.disconnected)

        val redirect = FailingConnection(URL("https://platform.example"), status = 302)
        val client = PlatformClient("https://platform.example") { redirect }
        try {
            client.heartbeat("secret", "terminal", 1)
            fail("Redirect was accepted")
        } catch (error: PlatformException) {
            assertEquals(302, error.statusCode)
        }
        assertFalse(redirect.instanceFollowRedirects)
        assertTrue(redirect.disconnected)
    }

    @Test
    fun expiredPrestartUsesStableReportAndRequiresPlatformSettlement() {
        val report = JSONObject()
            .put("report_id", "11111111-1111-4111-8111-111111111111")
            .put("attempt_id", "22222222-2222-4222-8222-222222222222")
            .put("package_id", "33333333-3333-4333-8333-333333333333")
            .put("offline_permit_id", "44444444-4444-4444-8444-444444444444")
            .put("occurred_at", "2026-09-28T00:00:00Z")
        val accepted = RecordingConnection(
            URL("https://platform.example"),
            JSONObject()
                .put("report_id", report.getString("report_id"))
                .put("disposition", "accepted")
                .put("execution_status", "ended")
                .put("attempt_status", "ended")
                .toString(),
        )
        val client = PlatformClient("https://platform.example") {
            accepted.requestedUrl = it
            accepted
        }
        assertEquals(PrestartSettlement.EXPIRED_FAILURE, client.settleExpiredPrestart("secret", report))
        assertEquals(
            "/api/v1/terminal/attempts/${report.getString("attempt_id")}/prestart-failure",
            accepted.requestedUrl.path,
        )
        assertEquals("POST", accepted.requestMethod)
        assertEquals("Bearer secret", accepted.getRequestProperty("Authorization"))
        val body = JSONObject(accepted.written.toString(Charsets.UTF_8.name()))
        assertEquals(report.getString("report_id"), body.getString("report_id"))
        assertFalse(body.has("attempt_id"))
        assertTrue(accepted.disconnected)

        val cancelled = RecordingConnection(
            URL("https://platform.example"),
            JSONObject()
                .put("report_id", report.getString("report_id"))
                .put("disposition", "stale")
                .put("execution_status", "cancelled")
                .put("attempt_status", "cancelled")
                .toString(),
        )
        assertEquals(
            PrestartSettlement.ALREADY_CANCELLED,
            PlatformClient("https://platform.example") { cancelled }
                .settleExpiredPrestart("secret", report),
        )

        val incomplete = RecordingConnection(URL("https://platform.example"), "{}")
        try {
            PlatformClient("https://platform.example") { incomplete }
                .settleExpiredPrestart("secret", report)
            fail("Unconfirmed settlement was accepted")
        } catch (error: PlatformException) {
            assertEquals("prestart_settlement_incomplete", error.code)
        }
    }

    private fun assertTransportFailure(action: () -> Unit) {
        try {
            action()
            fail("Expected transport failure")
        } catch (error: PlatformException) {
            assertEquals(0, error.statusCode)
            assertEquals("platform_unavailable", error.code)
        }
    }

    private class FailingConnection(
        url: URL,
        private val failWrite: Boolean = false,
        private val failRead: Boolean = false,
        private val status: Int = 200,
    ) : HttpURLConnection(url) {
        var disconnected = false

        override fun connect() = Unit
        override fun disconnect() { disconnected = true }
        override fun usingProxy() = false
        override fun getResponseCode(): Int = status
        override fun getOutputStream(): OutputStream = object : OutputStream() {
            override fun write(value: Int) {
                if (failWrite) throw IOException("write failed")
            }
        }
        override fun getInputStream() = if (failRead) {
            throw IOException("read failed")
        } else {
            ByteArrayInputStream("{}".toByteArray())
        }
    }

    private class RecordingConnection(url: URL, private val response: String) : HttpURLConnection(url) {
        val written = ByteArrayOutputStream()
        lateinit var requestedUrl: URL
        var disconnected = false

        override fun connect() = Unit
        override fun disconnect() { disconnected = true }
        override fun usingProxy() = false
        override fun getResponseCode() = 200
        override fun getOutputStream(): OutputStream = written
        override fun getInputStream() = ByteArrayInputStream(response.toByteArray(Charsets.UTF_8))
    }
}
