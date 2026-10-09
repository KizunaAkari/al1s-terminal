package com.al1s.terminal.runtime

import com.al1s.terminal.resources.CanonicalBodyStore
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.file.Files
import java.security.MessageDigest

class CanonicalBodyStoreTest {
    @Test fun largeBodyIsDurableAndVerifiedWithoutSqliteWindow() {
        val folder = Files.createTempDirectory("canonical-body-test").toFile()
        try {
            val body = "x".repeat(3 * 1024 * 1024)
            val hash = MessageDigest.getInstance("SHA-256").digest(body.toByteArray())
                .joinToString("") { "%02x".format(it) }
            val store = CanonicalBodyStore(folder)
            store.write(hash,body)
            assertEquals(body,CanonicalBodyStore(folder).read(hash,""))
            assertEquals("legacy",store.read(hash,"legacy"))
        } finally { folder.deleteRecursively() }
    }
}
