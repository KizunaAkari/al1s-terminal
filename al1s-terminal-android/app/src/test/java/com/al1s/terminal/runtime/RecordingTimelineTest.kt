package com.al1s.terminal.runtime

import com.al1s.terminal.media.RecordingTimeline
import org.junit.Assert.*
import org.junit.Test

class RecordingTimelineTest {
    @Test fun staticLastFrameContinuesUntilActualStopWithoutInventedFrames() {
        assertEquals(3500000L,RecordingTimeline.endUs(1000000000L,4500000000L,0))
        assertEquals(2000001L,RecordingTimeline.endUs(1000000000L,1200000000L,2000000L))
    }
}
