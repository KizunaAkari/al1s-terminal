package com.al1s.terminal.maa

import org.json.JSONObject

object MatchOffsetSelector {
    fun select(result:NativeRecognitionResult,preferred:Int):NativeRecognitionResult? {
        if(!result.hit)return null
        val detail=runCatching { JSONObject(result.detail) }.getOrDefault(JSONObject())
        val matches=detail.optJSONArray("filtered")?.takeIf { it.length()>0 } ?: detail.optJSONArray("all")
        if(matches==null || matches.length()==0)return result.takeIf { it.box.size==4 && it.box[2]>0 && it.box[3]>0 }
        val index=preferred.coerceIn(0,matches.length()-1)
        val selected=matches.getJSONObject(index)
        val array=selected.optJSONArray("box")
        val box=if(array!=null && array.length()==4) IntArray(4) { array.getInt(it) } else {
            val value=selected.getJSONObject("box");intArrayOf(value.getInt("x"),value.getInt("y"),value.getInt("width"),value.getInt("height"))
        }
        return NativeRecognitionResult(true,box,JSONObject().put("matches",matches.length()).put("selected_index",index)
            .put("preferred_index",preferred).put("score",selected.opt("score") ?: JSONObject.NULL).toString())
    }
}
