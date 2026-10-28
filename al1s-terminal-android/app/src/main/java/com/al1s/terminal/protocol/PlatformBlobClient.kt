package com.al1s.terminal.protocol

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

data class PlatformBlobMetadata(val sha256: String, val size: Long, val mediaType: String)

class PlatformBlobClient(baseUrl: String, private val factory: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection }) {
    private val origin = PlatformEndpoint.normalize(baseUrl)
    fun head(credential: String, blobId: String): PlatformBlobMetadata = request(credential, blobId, "HEAD") { connection ->
        checkStatus(connection, 200)
        val hash = connection.getHeaderField("X-Content-SHA256").orEmpty()
        val size = connection.getHeaderField("Content-Length")?.toLongOrNull() ?: 0
        val type = connection.getHeaderField("Content-Type").orEmpty().substringBefore(';').trim().lowercase()
        check(hash.matches(Regex("[0-9a-f]{64}")) && size in 1..(32L * 1024 * 1024) && type.isNotEmpty()) { "blob_metadata_invalid" }
        PlatformBlobMetadata(hash, size, type)
    }

    fun readRange(credential: String, blobId: String, start: Long, count: Int, total: Long): ByteArray {
        require(start >= 0 && count in 1..262144 && start + count <= total)
        return request(credential, blobId, "GET") { connection ->
            val end = start + count - 1
            connection.setRequestProperty("Range", "bytes=$start-$end")
            checkStatus(connection, 206)
            check(connection.getHeaderField("Content-Range") == "bytes $start-$end/$total") { "blob_range_mismatch" }
            connection.inputStream.use { input ->
                val bytes = ByteArray(count + 1)
                var received = 0
                while (received < bytes.size) {
                    val size = input.read(bytes, received, bytes.size - received)
                    if (size < 0) break
                    received += size
                }
                check(received == count) { "blob_range_incomplete" }
                bytes.copyOf(count)
            }
        }
    }

    private fun <T> request(credential: String, blobId: String, method: String, action: (HttpURLConnection) -> T): T {
        val id = UUID.fromString(blobId)
        val connection = factory(URL("$origin/api/v1/terminal/blobs/$id"))
        try {
            connection.instanceFollowRedirects = false
            connection.requestMethod = method
            connection.connectTimeout = 15000; connection.readTimeout = 15000
            connection.setRequestProperty("Authorization", "Bearer $credential")
            return action(connection)
        } catch (error: IOException) { throw PlatformException(0, "platform_unavailable", "Resource transfer unavailable", error) }
        finally { connection.disconnect() }
    }
    private fun checkStatus(connection: HttpURLConnection, expected: Int) {
        val status = connection.responseCode
        if (status != expected) throw PlatformException(status, "blob_http_error", "Resource returned HTTP $status")
    }
}
