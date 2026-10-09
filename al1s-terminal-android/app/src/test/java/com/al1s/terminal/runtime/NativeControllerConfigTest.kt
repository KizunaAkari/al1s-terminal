package com.al1s.terminal.runtime

import com.al1s.terminal.maa.NativeControllerConfig
import org.junit.Assert.*
import org.junit.Test

class NativeControllerConfigTest {
    @Test fun rawGeometryIsRequiredAndNoAutomaticScalingIsInserted() {
        val value = NativeControllerConfig("/data/app/test/lib/arm64/libMaaAndroidNativeControlUnit.so",1080,2400)
        assertEquals(1080,value.width)
        assertEquals(2400,value.height)
        assertTrue(value.json().contains("screen_resolution"))
        assertFalse(value.json().contains("short_side"))
    }
    @Test fun invalidGeometryOrNonLibraryPathIsRejected() {
        for (width in listOf(0,-1,20000)) {
            try { NativeControllerConfig("/data/app/test/lib.so",width,2400); fail("invalid geometry accepted") }
            catch (_: IllegalArgumentException) {}
        }
        try { NativeControllerConfig("https://not-local/lib.so",1080,2400); fail("remote path accepted") }
        catch (_: IllegalArgumentException) {}
    }
}
