package com.al1s.terminal.runtime

import com.al1s.terminal.media.ScrcpyVideoPackets
import java.io.*
import org.junit.Assert.*
import org.junit.Test

class ScrcpyVideoPacketsTest {
    @Test fun `actual scrcpy codec config and picture timestamp flags remain distinct`() {
        val bytes=ByteArrayOutputStream()
        DataOutputStream(bytes).use {
            it.writeInt(0x68323634);it.writeInt(720);it.writeInt(1600)
            it.writeLong(Long.MIN_VALUE);it.writeInt(3);it.write(byteArrayOf(1,2,3))
            it.writeLong((1L shl 62) or 1000L);it.writeInt(2);it.write(byteArrayOf(4,5))
        }
        val stream=ScrcpyVideoPackets(ByteArrayInputStream(bytes.toByteArray()))
        assertEquals(720,stream.width);assertTrue(stream.next().configuration)
        val frame=stream.next();assertFalse(frame.configuration);assertTrue(frame.keyframe);assertEquals(1000L,frame.timestamp)
    }
    @Test(expected=IllegalArgumentException::class) fun `oversized encoded packet is rejected before allocation`() {
        val bytes=ByteArrayOutputStream()
        DataOutputStream(bytes).use {it.writeInt(0x68323634);it.writeInt(720);it.writeInt(1600);it.writeLong(0);it.writeInt(9000000)}
        ScrcpyVideoPackets(ByteArrayInputStream(bytes.toByteArray())).next()
    }
}
