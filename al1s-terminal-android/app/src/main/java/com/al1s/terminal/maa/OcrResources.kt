package com.al1s.terminal.maa

import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.util.zip.ZipFile

/** Extract only the pinned CPU models from our APK, into a helper-owned directory. */
class OcrResources(private val apk: String) : AutoCloseable {
    private var directory: File? = null
    @Synchronized fun path(): String {
        directory?.let { return it.absolutePath }
        val folder = Files.createTempDirectory(File("/data/local/tmp").toPath(), "al1s-ocr-").toFile()
        try {
            ZipFile(apk).use { archive ->
                val manifest = archive.getInputStream(checkNotNull(archive.getEntry("assets/maa-ocr/manifest.json")))
                    .bufferedReader().use { org.json.JSONObject(it.readText()) }
                for (name in listOf("det.onnx", "rec.onnx", "keys.txt")) {
                    val entry = checkNotNull(archive.getEntry("assets/maa-ocr/$name"))
                    require(entry.size in 1..(32 * 1024 * 1024))
                    val file = File(folder, name)
                    archive.getInputStream(entry).use { input -> file.outputStream().use { input.copyTo(it) } }
                    val digest = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
                    check(digest == manifest.getString(name)) { "ocr_asset_hash_mismatch" }
                }
            }
            directory = folder
            return folder.absolutePath
        } catch (error: Exception) {
            listOf("det.onnx", "rec.onnx", "keys.txt").forEach { File(folder, it).delete() }
            folder.delete()
            throw error
        }
    }
    @Synchronized override fun close() {
        directory?.let { folder ->
            listOf("det.onnx", "rec.onnx", "keys.txt").forEach { File(folder, it).delete() }
            folder.delete()
        }
        directory = null
    }
}
