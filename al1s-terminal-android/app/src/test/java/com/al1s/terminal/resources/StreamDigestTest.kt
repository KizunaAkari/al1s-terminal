package com.al1s.terminal.resources

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test

class StreamDigestTest {
    @Test fun `copies bytes and returns lowercase sha256`() {
        val input = TrackingInput("abc".toByteArray())
        val output = TrackingOutput()

        val digest = copyWithSha256(input, output)

        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", digest)
        assertArrayEquals("abc".toByteArray(), output.toByteArray())
        assertFalse(input.closed)
        assertFalse(output.closed)
    }

    @Test fun `handles short reads with a bounded copy buffer`() {
        val source = ByteArray(1_000_003) { (it * 31).toByte() }
        val input = TrackingInput(source, maxChunk = 37)
        val output = TrackingOutput()

        copyWithSha256(input, output)

        val expectedDigest = "f80cf937bfbbf381219cb6daf5e662fb420258971913c415468d6b7dd9aba370"
        assertEquals(expectedDigest, copyWithSha256(source.inputStream(), ByteArrayOutputStream()))
        assertEquals(source.size, output.size())
        assertArrayEquals(source, output.toByteArray())
        assertEquals(8192, input.maxRequested)
        assertFalse(input.closed)
        assertFalse(output.closed)
    }

    @Test fun `propagates input failure without closing caller streams`() {
        val input = FailingInput()
        val output = TrackingOutput()

        assertThrows(IOException::class.java) { copyWithSha256(input, output) }

        assertFalse(input.closed)
        assertFalse(output.closed)
        assertArrayEquals(byteArrayOf(1, 2, 3), output.toByteArray())
    }

    private open class TrackingInput(private val source: ByteArray, private val maxChunk: Int = source.size) : InputStream() {
        var closed = false
        var maxRequested = 0
        private var position = 0

        override fun read(): Int = if (position == source.size) -1 else source[position++].toInt() and 0xff

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            maxRequested = maxOf(maxRequested, length)
            if (position == source.size) return -1
            val count = minOf(length, maxChunk, source.size - position)
            source.copyInto(buffer, offset, position, position + count)
            position += count
            return count
        }

        override fun close() { closed = true }
    }

    private class FailingInput : TrackingInput(byteArrayOf(1, 2, 3)) {
        private var failed = false

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            if (!failed && super.read(buffer, offset, length) >= 0) return 3
            failed = true
            throw IOException("input failed")
        }
    }

    private class TrackingOutput : ByteArrayOutputStream() {
        var closed = false

        override fun close() { closed = true; super.close() }
    }
}
