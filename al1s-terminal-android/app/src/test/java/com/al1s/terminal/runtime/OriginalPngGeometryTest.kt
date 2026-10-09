package com.al1s.terminal.runtime

import com.al1s.terminal.device.OriginalPngGeometry
import java.nio.ByteBuffer
import org.junit.Assert.*
import org.junit.Test

class OriginalPngGeometryTest {
    private fun png(width:Int,height:Int):ByteArray = ByteArray(33).also {
        byteArrayOf(137.toByte(),80,78,71,13,10,26,10).copyInto(it)
        "IHDR".toByteArray().copyInto(it,12)
        ByteBuffer.wrap(it).putInt(8,13).putInt(16,width).putInt(20,height)
    }
    @Test fun nativeGeometryIsPreservedAndAllocationIsBounded() {
        assertEquals(1080 to 2400,OriginalPngGeometry.require(png(1080,2400)))
        for(value in listOf(png(0,2400),png(8193,2),png(8192,8192),ByteArray(33))) {
            try {OriginalPngGeometry.require(value);fail("must reject invalid or unbounded original")}
            catch(expected:IllegalArgumentException){}
        }
    }
}
