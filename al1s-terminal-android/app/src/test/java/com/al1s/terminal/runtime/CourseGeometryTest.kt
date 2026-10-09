package com.al1s.terminal.runtime

import com.al1s.terminal.maa.CourseGeometry
import org.junit.Assert.*
import org.junit.Test

class CourseGeometryTest {
    @Test fun referenceBlocksAndClickPointsMatchLinuxContract() {
        for(block in 0..7) {
            val point=CourseGeometry.click(block,intArrayOf(2400,1080))
            assertEquals(block,CourseGeometry.block(point,intArrayOf(2400,1080)))
        }
        assertNull(CourseGeometry.block(intArrayOf(2200,1000,1,1),intArrayOf(2400,1080)))
    }
    @Test fun scaledScreensPreserveBlockAssociation() {
        val point=CourseGeometry.click(6,intArrayOf(1600,720))
        assertEquals(6,CourseGeometry.block(point,intArrayOf(1600,720)))
        assertEquals(410,point[0])
    }
}
