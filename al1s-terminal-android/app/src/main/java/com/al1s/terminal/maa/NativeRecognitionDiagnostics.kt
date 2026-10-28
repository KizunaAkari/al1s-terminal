package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

class NativeRecognitionDiagnostics(private val pipeline:JSONObject) {
    private data class Observation(var misses:Int=0,var best:Double?=null,var threshold:Double?=null)
    private val observations=mutableMapOf<Int,Observation>()
    @Synchronized fun observe(message:String,details:JSONObject) {
        if(message !in setOf("Node.Recognition.Failed","Node.Recognition.Succeeded"))return
        val node=pipeline.optJSONObject(details.optString("name")) ?: return
        if(node.optString("recognition") !in setOf("TemplateMatch","OCR"))return
        val metadata=node.optJSONObject("attach") ?: return
        if(metadata.has("popup_key"))return
        val step=metadata.opt("dsl_step_index") as? Int ?: return
        val value=observations.getOrPut(step) {Observation()}
        if(message.endsWith("Failed")) {
            if(value.misses==0)value.best=null
            value.misses++
        } else value.misses=0
        node.opt("threshold")?.let {if(it is Number && it.toDouble().isFinite())value.threshold=it.toDouble()}
        var scanned=0
        fun scan(item:Any?,depth:Int) {
            if(depth>15 || ++scanned>2000)return
            when(item) {
                is JSONObject -> item.keys().forEach {key->
                    val child=item.get(key)
                    if(key=="score" && child is Number && child.toDouble().isFinite() && child.toDouble() in 0.0..1.0)
                        value.best=maxOf(value.best ?: 0.0,child.toDouble())
                    else scan(child,depth+1)
                }
                is JSONArray -> for(index in 0 until minOf(item.length(),200))scan(item.get(index),depth+1)
            }
        }
        scan(details,0)
    }
    @Synchronized fun snapshot(step:Int?):JSONObject? {
        val value=observations[step] ?: return null
        return JSONObject().put("consecutive_misses",value.misses).apply {
            value.best?.let {put("actual_score",it)}
            value.threshold?.let {put("configured_threshold",it)}
        }
    }
}
