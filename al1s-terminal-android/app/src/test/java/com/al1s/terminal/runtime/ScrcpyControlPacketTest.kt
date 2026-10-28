package com.al1s.terminal.runtime

import com.al1s.terminal.media.ScrcpyControlPacket
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer

class ScrcpyControlPacketTest {
    private fun touch(x: Int = 100, width: Int = 1080) = ByteBuffer.allocate(32)
        .put(2).put(0).putLong(0).putInt(x).putInt(200).putShort(width.toShort())
        .putShort(2400.toShort()).putShort(65535.toShort()).putInt(0).putInt(0).array()

    @Test fun `same supported packets and bounds as Linux`() {
        assertTrue(ScrcpyControlPacket.accepts(touch()))
        assertFalse(ScrcpyControlPacket.accepts(touch(x = 1080)))
        assertFalse(ScrcpyControlPacket.accepts(touch(width = 0)))
        assertTrue(ScrcpyControlPacket.accepts(ByteBuffer.allocate(14).put(0).put(0)
            .putInt(4).putInt(0).putInt(0).array()))
    }

    @Test fun `reject clipboard shell and invalid packet lengths`() {
        assertFalse(ScrcpyControlPacket.accepts(byteArrayOf(8, 0, 0)))
        assertFalse(ScrcpyControlPacket.accepts(ByteArray(1024)))
        assertFalse(ScrcpyControlPacket.accepts(byteArrayOf()))
    }
}
