package com.al1s.terminal.maa

import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import org.json.JSONObject

/** One native execution's bounded immutable evidence; acknowledgement owns deletion. */
class NativeEvidenceStore(private val port: MaaContextAccess) {
    private var directory: File? = null
    private var count = 0
    private var bytes = 0L
    @Synchronized fun capture(context: Long, node: String): JSONObject {
        require(count < 64 && bytes < 256L * 1024 * 1024) { "native_evidence_spool_full" }
        val image = port.capture(context)
        check(image != 0L) { "evidence_capture_failed" }
        val data = try { port.encodedImage(image) } finally { port.destroyImage(image) }
        require(data.size in 8..(32 * 1024 * 1024) && bytes + data.size <= 256L * 1024 * 1024)
        check(data.take(8) == listOf(137, 80, 78, 71, 13, 10, 26, 10).map { it.toByte() }) { "evidence_frame_not_png" }
        val folder = directory ?: Files.createTempDirectory(File("/data/local/tmp").toPath(), "al1s-evidence-").toFile().also { directory = it }
        val id = java.util.UUID.randomUUID().toString()
        val file = File(folder, "$id.png")
        java.io.FileOutputStream(file).use { it.write(data); it.fd.sync() }
        count++; bytes += data.size
        val digest = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
        return JSONObject().put("artifact_id", id).put("path", file.absolutePath).put("mime", "image/png")
            .put("size_bytes", data.size).put("sha256", digest).put("node", node)
    }
}
