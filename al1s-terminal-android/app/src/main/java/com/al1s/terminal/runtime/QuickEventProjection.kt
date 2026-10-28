package com.al1s.terminal.runtime

import org.json.JSONObject

data class ProjectedQuickEvent(val kind:String,val step:Int?,val code:String?)

object QuickEventProjection {
    fun project(message:String,event:JSONObject,definition:String,selectedStep:Int?):ProjectedQuickEvent? {
        val name=event.optString("name")
        val rule=Regex("^Global_(\\d+)_").find(name)?.groupValues?.get(1)?.toIntOrNull()
        if(rule!=null && message in setOf("AL1S.Rule.Clicked","AL1S.Rule.Limit","AL1S.Rule.Timeout")) {
            val status=if(message=="AL1S.Rule.Clicked")"succeeded" else "failed"
            return ProjectedQuickEvent("log",null,"maa_rule_$status:$definition:$rule")
        }
        val step=Regex("^Step_(\\d{3})$").matchEntire(name)?.groupValues?.get(1)?.toIntOrNull() ?: return null
        val kind=when(message) {
            "Node.Action.Starting"->"step_started"
            "Node.Action.Succeeded"->"step_succeeded"
            "Node.Action.Failed"->"step_failed"
            else->return null
        }
        return ProjectedQuickEvent(kind,selectedStep ?: step+1,null)
    }
}
