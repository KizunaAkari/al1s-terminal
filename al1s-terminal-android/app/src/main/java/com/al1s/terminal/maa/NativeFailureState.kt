package com.al1s.terminal.maa

import org.json.JSONObject

/** Attribute native failure to the frozen target, ignoring independent-rule failures. */
class NativeFailureState(private val pipeline: JSONObject) {
    private var pending: Pair<String, Int>? = null
    private val misses = mutableSetOf<Int>()
    private var failure: Triple<Int, String, String>? = null
    private var activeAction:Triple<Int,String,String>?=null
    val recognition = NativeRecognitionDiagnostics(pipeline)
    private var ruleFailure:JSONObject?=null

    @Synchronized fun observe(message: String, details: JSONObject) {
        recognition.observe(message,details)
        val name = details.optString("name")
        val node = pipeline.optJSONObject(name)
        val metadata = node?.optJSONObject("attach")
        val index = metadata?.opt("dsl_step_index") as? Int
        val rule = metadata?.has("popup_key") == true
        when (message) {
            "AL1S.Rule.Timeout", "AL1S.Rule.Limit" -> ruleFailure=JSONObject(details.toString())
            "Node.Action.Starting" -> if(index!=null && !rule && metadata.optString("maa_project_role") !in setOf("failure-skip","end"))
                activeAction=Triple(index,"execution","action_started")
            "Node.Action.Succeeded" -> if(index!=null && activeAction?.first==index)activeAction=null
            "Node.Recognition.Failed", "Node.Recognition.Succeeded" -> if (index != null && !rule &&
                node.optString("recognition") in setOf("TemplateMatch", "OCR")) {
                if (message.endsWith("Failed")) misses += index else misses -= index
            }
            "Node.NextList.Starting" -> {
                val list = details.optJSONArray("list") ?: return
                for (position in 0 until list.length()) {
                    val raw = list.get(position)
                    val target = if (raw is JSONObject) raw.optString("name") else raw as? String
                    val info = pipeline.optJSONObject(target.orEmpty())?.optJSONObject("attach") ?: continue
                    val targetIndex = info.opt("dsl_step_index") as? Int ?: continue
                    if (info.optString("maa_project_role") != "failure-skip" && !info.has("popup_key")) {
                        pending = name to targetIndex
                        break
                    }
                }
            }
            "Node.Action.Failed" -> {
                if(rule)ruleFailure=JSONObject(details.toString())
                else if(index!=null && metadata.optString("maa_project_role")!="failure-skip") {
                    if(failure?.first!=index || failure?.third!="step_timeout")
                        failure=Triple(index,if(index in misses)"recognition" else "execution","action_failed")
                }
            }
            "Node.PipelineNode.Failed" -> {
                val waiting = pending
                if (waiting != null && name == waiting.first && details.optJSONObject("node_details")?.optString("name").isNullOrEmpty())
                    failure = Triple(waiting.second, "recognition", "step_timeout")
            }
            "AL1S.Step.Timeout" -> {
                val step=details.getInt("step_index")
                failure=Triple(step,if(activeAction?.first==step)"execution" else "recognition","step_timeout")
            }
        }
    }

    @Synchronized fun matches(index: Int, phase: String) = failure?.let { it.first == index && it.second == phase } == true
    @Synchronized fun independentRuleFailure():JSONObject? = ruleFailure?.let {JSONObject(it.toString())}
    @Synchronized fun snapshot(): Triple<Int, String, String>? = failure ?: activeAction ?: pending?.let { Triple(it.second, "recognition", "pending_recognition") }
    @Synchronized fun consume(index: Int, phase: String): Boolean {
        if (!matches(index, phase)) return false
        failure = null
        pending = null
        activeAction=null
        misses -= index
        return true
    }
}
