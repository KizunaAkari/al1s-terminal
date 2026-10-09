package com.al1s.terminal.execution

import com.al1s.terminal.device.DeviceCommand
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID

object FailureEvidence {
    fun capture(directory: File): JSONObject {
        val png = DeviceCommand.run(listOf("/system/bin/screencap", "-p"), 16 * 1024 * 1024)
        require(png.size >= 24 && png.take(8) == listOf(137,80,78,71,13,10,26,10).map { it.toByte() })
        val id = UUID.randomUUID().toString()
        val file = File(directory, "$id.png")
        FileOutputStream(file).use { it.write(png); it.fd.sync() }
        val dimensions = java.nio.ByteBuffer.wrap(png)
        return JSONObject().put("artifact_id", id).put("path", file.absolutePath).put("mime", "image/png")
            .put("size_bytes", png.size).put("width", dimensions.getInt(16)).put("height", dimensions.getInt(20))
            .put("sha256", MessageDigest.getInstance("SHA-256").digest(png).joinToString("") { "%02x".format(it) })
    }
}
