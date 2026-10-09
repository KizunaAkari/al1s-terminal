package com.al1s.terminal.runtime

import org.json.JSONObject

object QuickDiagnosticProjection {
    fun restoreStep(detail:JSONObject,selected:Int?) {
        val diagnosis=detail.optJSONObject("failure_diagnosis")
        if(selected==null || diagnosis?.optString("stage")=="independent_rule" ||
            diagnosis?.has("independent_rule")==true)return
        detail.optJSONObject("failed_step")?.put("number",selected)?.put("index",selected-1)
    }
}
