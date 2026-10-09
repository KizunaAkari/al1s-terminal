package com.al1s.terminal.runtime

import com.al1s.terminal.device.ScreenFrameCapture
import org.junit.Assert.*
import org.junit.Test

class ScreenFrameCaptureTest {
    @Test fun retriesBlackFirstDrawWithoutSendingAnyInput() {
        var captures=0;var now=0L
        val result=ScreenFrameCapture.capture(
            snapshot={captures++;byteArrayOf((if(captures==1)0 else 1).toByte())},
            blank={it[0].toInt()==0},now={now},pause={now+=100})
        assertEquals(1,result[0].toInt());assertEquals(2,captures)
    }
    @Test fun legitimateBlackFrameHasABoundedGracePeriod() {
        var now=0L
        val result=ScreenFrameCapture.capture(snapshot={byteArrayOf(0)},blank={true},
            now={now},pause={now+=100})
        assertEquals(0,result[0].toInt());assertTrue(now<=2000)
    }
}
