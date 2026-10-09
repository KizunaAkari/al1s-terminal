package com.al1s.terminal.media

object RecordingTimeline {
    fun endUs(firstFrameNanos:Long,stopNanos:Long,lastPresentationUs:Long):Long =
        maxOf(lastPresentationUs+1,(stopNanos-firstFrameNanos).coerceAtLeast(0)/1000)
}
