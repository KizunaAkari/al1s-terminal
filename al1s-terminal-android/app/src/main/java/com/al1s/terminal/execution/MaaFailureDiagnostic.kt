package com.al1s.terminal.execution

import org.json.JSONArray
import org.json.JSONObject

object MaaFailureDiagnostic {
    fun build(scriptName: String, version: String, moduleIndex: Int, stepIndex: Int?, phase: String, code: String): JSONObject {
        val title = when (code) {
            "maa_step_execution_stalled" -> "步骤执行超过时间预算"
            "task_timeout" -> "任务执行超过时间预算"
            "execution_cancelled" -> "任务已取消"
            else -> if (phase == "recognition") "目标识别失败" else "步骤执行失败"
        }
        val result = JSONObject().put("success", false).put("error", code).put("failure_diagnosis", JSONObject()
            .put("stage", phase).put("title", title).put("message", "脚本“$scriptName”${stepIndex?.let { "第${it+1}步" } ?: ""}：$title"))
        stepIndex?.let { result.put("failed_step", JSONObject().put("number", it+1).put("index", it)) }
        return JSONObject().put("modules", JSONArray().put(JSONObject().put("module_index", moduleIndex+1)
            .put("script_name", scriptName).put("script_version_id", version).put("result", result)))
    }
}
