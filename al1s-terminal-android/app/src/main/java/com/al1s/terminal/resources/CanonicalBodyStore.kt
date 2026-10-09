package com.al1s.terminal.resources

import java.io.File
import java.security.MessageDigest

/** Large canonical bodies stay outside SQLite CursorWindow, keyed by the typed hash. */
class CanonicalBodyStore(private val root: File) {
    private val resources = VerifiedResourceStore(root)
    fun write(hash: String, body: String): File {
        val bytes = body.toByteArray(Charsets.UTF_8)
        require(bytes.size in 1..(4 * 1024 * 1024))
        return resources.receive(hash, bytes.size.toLong()) { offset, length ->
            bytes.copyOfRange(offset.toInt(), offset.toInt() + length)
        }
    }
    fun read(hash: String, legacy: String): String {
        if (legacy.isNotEmpty()) return legacy
        require(hash.matches(Regex("[0-9a-f]{64}")))
        val file = File(root, hash)
        require(file.length() in 1..(4 * 1024 * 1024))
        val verified = checkNotNull(resources.ready(hash, file.length())) { "canonical_body_not_ready" }
        return verified.readText(Charsets.UTF_8)
    }
}
