package com.al1s.terminal.resources

import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

/** Content identity owns names; partial transfers are never returned to a caller. */
class VerifiedResourceStore(private val directory: File) {
    @Synchronized fun ready(sha256: String, size: Long): File? {
        validate(sha256, size)
        val file = File(directory, sha256)
        return file.takeIf { it.isFile && !Files.isSymbolicLink(it.toPath()) && it.length() == size && digest(it) == sha256 }
    }

    @Synchronized fun receive(sha256: String, size: Long, read: (Long, Int) -> ByteArray): File {
        validate(sha256, size)
        ready(sha256, size)?.let { return it }
        check(directory.isDirectory || directory.mkdirs()) { "resource_directory_unavailable" }
        val temporary = Files.createTempFile(directory.toPath(), ".receiving-", ".part").toFile()
        try {
            FileOutputStream(temporary).use { output ->
                var position = 0L
                while (position < size) {
                    val count = minOf(262144L, size - position).toInt()
                    val bytes = read(position, count)
                    check(bytes.size == count) { "resource_range_incomplete" }
                    output.write(bytes)
                    position += count
                }
                output.fd.sync()
            }
            check(temporary.length() == size && digest(temporary) == sha256) { "resource_hash_mismatch" }
            val file = File(directory, sha256)
            Files.move(temporary.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            return file
        } finally { temporary.delete() }
    }

    private fun validate(hash: String, size: Long) {
        require(hash.matches(Regex("[0-9a-f]{64}")) && size in 1..(32L * 1024 * 1024)) { "resource_manifest_invalid" }
    }
    private fun digest(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) { val size = input.read(buffer); if (size < 0) break; digest.update(buffer, 0, size) }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
