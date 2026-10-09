package com.al1s.terminal.runtime

import com.al1s.terminal.maa.MatchOffsetSelector
import com.al1s.terminal.maa.NativeRecognitionResult
import org.junit.Assert.*
import org.junit.Test

class MatchOffsetSelectorTest {
    @Test fun `selected target follows fresh filtered results and clamps unavailable preferred index`() {
        val result=NativeRecognitionResult(true,intArrayOf(1,2,3,4),"""{"filtered":[{"box":[10,20,30,40],"score":0.9},{"box":[100,200,30,40],"score":0.95}]}""")
        assertArrayEquals(intArrayOf(100,200,30,40),MatchOffsetSelector.select(result,1)!!.box)
        assertArrayEquals(intArrayOf(100,200,30,40),MatchOffsetSelector.select(result,5)!!.box)
    }
    @Test fun `missing native recognition does not fabricate a target`() {
        assertNull(MatchOffsetSelector.select(NativeRecognitionResult(false,intArrayOf(0,0,0,0),"{}"),0))
    }
}
