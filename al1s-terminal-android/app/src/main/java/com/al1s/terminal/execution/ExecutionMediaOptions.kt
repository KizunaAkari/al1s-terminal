package com.al1s.terminal.execution

import org.json.JSONObject

object ExecutionMediaOptions {
    fun recordVideo(body:JSONObject):Boolean = if(body.optString("owner_kind")=="quick_test")false
        else body.optBoolean("record_video",false)
}
