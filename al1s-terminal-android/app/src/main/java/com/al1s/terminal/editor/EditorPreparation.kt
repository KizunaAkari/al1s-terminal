package com.al1s.terminal.editor

import org.json.JSONArray
import org.json.JSONObject

object EditorPreparation {
    fun requireFrameReady(prepared:Boolean, automation:Boolean) {
        check(prepared || automation) { "editor_input_authority_unavailable" }
    }

    fun needsPathRefresh(sessions:JSONArray, deviceId:String):Boolean =
        (0 until sessions.length()).any {
            val session=sessions.getJSONObject(it)
            session.optString("device_id")==deviceId &&
                session.optString("status") in setOf("pending","active")
        }

    fun activationAccepted(reply:JSONObject):Boolean = reply.optString("status")=="active"
}
