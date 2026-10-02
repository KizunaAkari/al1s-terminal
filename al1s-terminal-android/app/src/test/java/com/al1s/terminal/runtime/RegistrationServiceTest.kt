package com.al1s.terminal.runtime

import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.security.RegistrationIdentityStore
import com.al1s.terminal.security.TerminalConnectionGate
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Test

class RegistrationServiceTest {
    private class Store : RegistrationIdentityStore {
        var connection = "https://a.example" to "a-credential"
        var commits = 0
        override fun installationId() = "installation"
        override fun saveRegistration(baseUrl: String, displayName: String, terminalId: String,
            targetDeviceId: String, credential: String, terminalRowVersion: Int) {
            TerminalConnectionGate.withConnection { connection = baseUrl to credential; commits++ }
        }
    }
    private class Reply(url: URL, private val status: Int) : HttpURLConnection(url) {
        override fun connect() {}
        override fun disconnect() {}
        override fun usingProxy() = false
        override fun getResponseCode() = status
        override fun getOutputStream() = ByteArrayOutputStream()
        override fun getInputStream() = ByteArrayInputStream(
            """{"terminal":{"terminal_id":"b","row_version":1},"target_device":{"device_id":"b-device"},"credential":"b-credential"}""".toByteArray()
        )
        override fun getErrorStream() = ByteArrayInputStream("{}".toByteArray())
    }
    @Test fun failedRegistrationNeverChangesThePreviousOriginOrCredential() {
        for (failure in listOf(400, 503)) {
            val store = Store()
            val service = RegistrationService(store) { base -> PlatformClient(base) { Reply(it, failure) } }
            try { service.register("https://b.example", "bad-code", "B"); fail("Registration succeeded") }
            catch (_: Exception) {}
            assertEquals("https://a.example" to "a-credential", store.connection)
            assertEquals(0, store.commits)
        }
    }
    @Test fun successCommitsAnOriginAndIdentityTogetherAfterTheRequest() {
        val store = Store()
        val service = RegistrationService(store) { base ->
            assertEquals("https://a.example" to "a-credential", store.connection)
            PlatformClient(base) { Reply(it, 201) }
        }
        assertEquals("b-device", service.register("https://b.example/", "code", " B "))
        assertEquals("https://b.example" to "b-credential", store.connection)
        assertEquals(1, store.commits)
    }
    @Test fun aRegistrationTimeoutKeepsThePreviousConnection() {
        val store = Store()
        val service = RegistrationService(store) { base -> PlatformClient(base) {
            throw java.net.SocketTimeoutException("timeout")
        } }
        try { service.register("https://b.example", "code", "B"); fail("Registration succeeded") }
        catch (_: Exception) {}
        assertEquals("https://a.example" to "a-credential", store.connection)
        assertEquals(0, store.commits)
    }
    @Test fun aRegistrationCommitWaitsForTheWholeSyncConnectionSession() {
        val store = Store()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val networkDone = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        try {
            val sync = executor.submit {
                TerminalConnectionGate.withConnection {
                    entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS))
                    assertEquals("https://a.example" to "a-credential", store.connection)
                }
            }
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            val register = executor.submit {
                RegistrationService(store) { base -> PlatformClient(base) {
                    networkDone.countDown(); Reply(it, 201)
                } }.register("https://b.example", "code", "B")
            }
            assertTrue(networkDone.await(5, TimeUnit.SECONDS))
            assertEquals("https://a.example" to "a-credential", store.connection)
            release.countDown(); sync.get(5, TimeUnit.SECONDS); register.get(5, TimeUnit.SECONDS)
            assertEquals("https://b.example" to "b-credential", store.connection)
        } finally { release.countDown(); executor.shutdownNow() }
    }
}
