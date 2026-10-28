package com.al1s.terminal.runtime

import com.al1s.terminal.media.AvcConfiguration
import org.junit.Assert.*
import org.junit.Test

class AvcConfigurationTest {
    @Test fun annexBSpsAndPpsBecomeSeparateAndroidCodecBuffers() {
        val raw=byteArrayOf(0,0,0,1,0x67,0x64,1,0,0,1,0x68,2)
        val (sps,pps)=AvcConfiguration.split(raw)
        assertArrayEquals(byteArrayOf(0,0,0,1,0x67,0x64,1),sps)
        assertArrayEquals(byteArrayOf(0,0,0,1,0x68,2),pps)
    }
    @Test(expected=IllegalArgumentException::class) fun incompleteConfigurationIsRejected() {
        AvcConfiguration.split(byteArrayOf(0,0,0,1,0x67,0x64))
    }
}
