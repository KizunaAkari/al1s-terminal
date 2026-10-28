package com.al1s.terminal.runtime

import com.al1s.terminal.editor.EditorPreparation
import org.junit.Test
import org.junit.Assert.*
import org.json.JSONArray
import org.json.JSONObject

class EditorPreparationTest {
    @Test fun preparedOrAutomationAllowsFrames() {
        EditorPreparation.requireFrameReady(true,false)
        EditorPreparation.requireFrameReady(false,true)
    }
    @Test(expected=IllegalStateException::class) fun missingInputGrantMustNotReadBlackFrame() {
        EditorPreparation.requireFrameReady(false,false)
    }
    @Test fun onlyThisDevicesLiveEditorWorkRefreshesAuthority() {
        for(status in listOf("pending","active","closing","closed","expired")) {
            val rows=JSONArray().put(JSONObject().put("device_id","phone").put("status",status))
            assertEquals(status in setOf("pending","active"),EditorPreparation.needsPathRefresh(rows,"phone"))
            assertFalse(EditorPreparation.needsPathRefresh(rows,"another-phone"))
        }
        assertFalse(EditorPreparation.needsPathRefresh(JSONArray(),"phone"))
    }
    @Test fun lateOrRejectedActivationCannotOpenMedia() {
        for(status in listOf("pending","closing","closed","expired","failed"))
            assertFalse(EditorPreparation.activationAccepted(JSONObject().put("status",status)))
        assertFalse(EditorPreparation.activationAccepted(JSONObject()))
        assertTrue(EditorPreparation.activationAccepted(JSONObject().put("status","active")))
    }
}
