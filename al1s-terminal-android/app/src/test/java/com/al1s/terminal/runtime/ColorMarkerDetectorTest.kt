package com.al1s.terminal.runtime

import com.al1s.terminal.maa.ColorMarkerDetector
import org.junit.Assert.*
import org.junit.Test

class ColorMarkerDetectorTest {
    @Test fun `three yellow bars produce student target rather than clicking the bars`() {
        val pixels = ByteArray(480*320*3)
        fun bar(x: Int,y: Int) { for (row in y until y+10) for(col in x until x+30) {
            val i=(row*480+col)*3;pixels[i]=0;pixels[i+1]=255.toByte();pixels[i+2]=255.toByte()
        } }
        bar(100,100);bar(90,130);bar(100,160)
        val markers = ColorMarkerDetector.find(pixels,480,320,org.json.JSONObject("""{"anchor":"top_left","offset_x":45,"offset_y":110}"""))
        assertEquals(1, markers.size)
        assertArrayEquals(intArrayOf(135,210),markers.single().target)
    }
    @Test fun `ordinary yellow UI without three compact components is not a student marker`() {
        val pixels=ByteArray(480*320*3)
        for(row in 100..110) for(col in 100..130) {val i=(row*480+col)*3;pixels[i+1]=255.toByte();pixels[i+2]=255.toByte()}
        assertTrue(ColorMarkerDetector.find(pixels,480,320,org.json.JSONObject()).isEmpty())
    }
}
