package com.al1s.terminal.runtime

import org.json.JSONObject

object QuickPreflightResult {
    fun failure(error:IllegalArgumentException):JSONObject = JSONObject().put("passed",false)
        .put("error_code","maa_definition_unsupported").put("diagnostic",JSONObject().put("preflight_failed",true)
            .put("failure_diagnosis",JSONObject().put("stage","preparation").put("title","脚本校验失败")
                .put("message",error.message?.take(1000) ?: "不支持当前脚本定义")))
    fun isLocalFailure(result:JSONObject):Boolean = result.opt("passed")==false &&
        result.optString("error_code")=="maa_definition_unsupported" &&
        result.optJSONObject("diagnostic")?.opt("preflight_failed")==true
}
