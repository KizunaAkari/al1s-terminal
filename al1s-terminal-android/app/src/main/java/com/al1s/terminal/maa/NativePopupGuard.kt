package com.al1s.terminal.maa

import org.json.JSONObject

class NativePopupGuard(private val port:MaaContextAccess,private val events:MaaEventProjection,private val budget:NativeBudgetHandler) {
    private val counts=mutableMapOf<String,Int>()
    fun recognize(context:Long,node:String,config:JSONObject,image:Long):NativeRecognitionResult? {
        if(budget.fatal || port.stopping(context))return null
        val result=port.recognize(context,config.getString("source"),image)
        if(!result.hit)return null
        if((counts[config.getString("key")] ?: 0)>=10) {
            budget.markFatal()
            events.onEvent("AL1S.Rule.Limit",JSONObject().put("name",node).put("rule_key",config.getString("key")).put("successful_clicks",10).toString())
            return null
        }
        return result
    }
    fun action(context:Long,node:String,config:JSONObject,box:IntArray):Boolean {
        if(budget.fatal || port.stopping(context))return false
        val source=config.getString("source");val key=config.getString("key")
        val metadata=JSONObject(port.nodeData(context,source)).optJSONObject("attach")
        val target=if(metadata?.optBoolean("click_match_center")==true && box[2]>0 && box[3]>0)
            intArrayOf(box[0]+(box[2]-1)/2,box[1]+(box[3]-1)/2,1,1) else box
        val success=port.action(context,source,target,"{}")
        if(success) {
            counts[key]=(counts[key] ?: 0)+1
            events.onEvent("AL1S.Rule.Clicked",JSONObject().put("name",node).put("rule_key",key).put("successful_clicks",counts[key]).toString())
        }
        return success
    }
}
