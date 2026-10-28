package com.al1s.terminal.runtime

import com.al1s.terminal.device.NativeFramePixels
import org.junit.Assert.assertArrayEquals
import org.junit.Test

class NativeFramePixelsTest {
    @Test fun `native ABI receives BGR rather than ARGB or RGBA`() {
        assertArrayEquals(byteArrayOf(0x33, 0x22, 0x11, 0x66, 0x55, 0x44),
            NativeFramePixels.bgr(intArrayOf(0xff112233.toInt(), 0x80445566.toInt()), 2, 1))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `reject frame dimensions inconsistent with captured pixels`() {
        NativeFramePixels.bgr(intArrayOf(0), 2, 1)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `reject unbounded frame allocation`() {
        NativeFramePixels.bgr(IntArray(0), 16384, 16384)
    }
}
