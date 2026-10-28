package com.al1s.terminal.protocol

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest

class PackageCanonicalTextTest {
    private fun hash(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
    @Test fun `Python canonical text preserves float spelling and is the consumed body`() {
        val canonical = """{"large":1e+20,"small":1e-06,"whole":1.0,"zero":-0.0}"""
        val pkg = TaskPackage("id", "attempt", hash(canonical), JSONObject(canonical), canonical)
        assertEquals(canonical, PackageCanonicalText.verified(pkg))
    }
    @Test fun `valid hash cannot authorize a different body`() {
        val canonical = """{"value":1.0}"""
        val pkg = TaskPackage("id", "attempt", hash(canonical), JSONObject("""{"value":2.0}"""), canonical)
        assertThrows(IllegalArgumentException::class.java) { PackageCanonicalText.verified(pkg) }
    }
    @Test fun `legacy integer demo retains its original hash contract`() {
        val body = JSONObject("""{"a":1,"b":"test"}""")
        val canonical = CanonicalJson.encode(body)
        assertEquals(canonical, PackageCanonicalText.verified(TaskPackage("id", "attempt", hash(canonical), body)))
    }
}
