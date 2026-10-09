package com.al1s.terminal.runtime

import com.al1s.terminal.device.DeviceGeometry
import org.junit.Assert.*
import org.junit.Test

class DeviceGeometryTest {
    @Test fun `rotation invalidates an old screenshot input generation`() {
        val portrait = DeviceGeometry(1080, 2400, 0, 1)
        assertTrue(portrait.accepts(1, 1080, 2400, 20, 30))
        val landscape = DeviceGeometry(2400, 1080, 1, 2)
        assertFalse(landscape.accepts(1, 1080, 2400, 20, 30))
        assertTrue(landscape.accepts(2, 2400, 1080, 20, 30))
        assertFalse(landscape.accepts(2, 2400, 1080, 2400, 30))
    }
}
