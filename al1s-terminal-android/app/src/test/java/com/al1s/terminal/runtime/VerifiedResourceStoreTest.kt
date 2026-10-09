package com.al1s.terminal.runtime

import com.al1s.terminal.resources.VerifiedResourceStore
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files
import java.security.MessageDigest

class VerifiedResourceStoreTest {
    private fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    @Test fun `only complete hash-verified data becomes available and repeated receive reuses it`() {
        val root = Files.createTempDirectory("al1s-resource-test").toFile()
        val bytes = ByteArray(300000) { (it % 251).toByte() }
        try {
            val store = VerifiedResourceStore(root)
            var downloads = 0
            val file = store.receive(hash(bytes), bytes.size.toLong()) { start, count ->
                downloads++; bytes.copyOfRange(start.toInt(), start.toInt() + count)
            }
            assertArrayEquals(bytes, file.readBytes())
            assertEquals(2, downloads)
            store.receive(hash(bytes), bytes.size.toLong()) { _, _ -> error("already verified") }
            assertEquals(2, downloads)
        } finally { root.deleteRecursively() }
    }
    @Test fun `interrupted or incorrect download never exposes a ready file`() {
        val root = Files.createTempDirectory("al1s-resource-test").toFile()
        val bytes = byteArrayOf(1, 2, 3)
        try {
            val store = VerifiedResourceStore(root)
            assertThrows(IllegalStateException::class.java) { store.receive(hash(bytes), 3) { _, _ -> byteArrayOf(0, 0, 0) } }
            assertNull(store.ready(hash(bytes), 3))
            assertThrows(IllegalArgumentException::class.java) { store.receive("../unsafe", 3) { _, _ -> bytes } }
        } finally { root.deleteRecursively() }
    }
}
