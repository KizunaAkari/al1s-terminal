package com.al1s.terminal.runtime

import com.al1s.terminal.execution.ExecutionMediaOptions
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ExecutionMediaOptionsTest {
    @Test fun formalRecordingUsesFrozenPackageSelection() {
        assertTrue(ExecutionMediaOptions.recordVideo(JSONObject().put("record_video",true).put("manifest",JSONObject())))
        assertFalse(ExecutionMediaOptions.recordVideo(JSONObject().put("record_video",false)
            .put("manifest",JSONObject().put("record_video",true))))
    }
    @Test fun absentPackageSelectionDoesNotEnableRecording() {
        assertFalse(ExecutionMediaOptions.recordVideo(JSONObject().put("manifest",JSONObject().put("record_video",true))))
    }
    @Test fun debugBodyExplicitlyDisablesRecording() {
        assertFalse(ExecutionMediaOptions.recordVideo(JSONObject().put("owner_kind","quick_test")
            .put("record_video",false).put("manifest",JSONObject().put("record_video",true))))
    }
}
