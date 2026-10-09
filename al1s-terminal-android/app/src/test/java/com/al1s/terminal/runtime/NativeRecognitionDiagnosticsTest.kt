package com.al1s.terminal.runtime

import com.al1s.terminal.maa.NativeRecognitionDiagnostics
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NativeRecognitionDiagnosticsTest {
    @Test fun failureScoreDoesNotReuseAnEarlierSuccessfulFrame() {
        val pipeline=JSONObject("""{"Source":{"recognition":"TemplateMatch","threshold":0.85,"attach":{"dsl_step_index":3}}}""")
        val diagnosis=NativeRecognitionDiagnostics(pipeline)
        diagnosis.observe("Node.Recognition.Succeeded",JSONObject("""{"name":"Source","details":{"all":[{"score":0.99}]}}"""))
        diagnosis.observe("Node.Recognition.Failed",JSONObject("""{"name":"Source","details":{"all":[{"score":0.31}]}}"""))
        val result=checkNotNull(diagnosis.snapshot(3))
        assertEquals(0.31,result.getDouble("actual_score"),0.00001)
        assertEquals(1,result.getInt("consecutive_misses"))
        assertEquals(0.85,result.getDouble("configured_threshold"),0.00001)
    }
}
