package com.al1s.terminal.maa

import org.json.JSONObject

class ColorMarkerRecognition(private val port: MaaContextAccess, private val events: MaaEventProjection) {
    private val misses=mutableMapOf<String,Int>()
    fun recognize(node:String,config:JSONObject,image:Long,roi:IntArray):NativeRecognitionResult? {
        val key=config.optString("state_key",node)
        if(config.optString("mode","target")=="absent") {
            val count=misses[key] ?: 0
            return if(count>=config.optInt("absence_checks",3)) NativeRecognitionResult(true,intArrayOf(0,0,1,1),
                JSONObject().put("absence_checks",count).put("stable_absence",true).toString()) else null
        }
        val png=port.encodedImage(image)
        val bitmap=checkNotNull(android.graphics.BitmapFactory.decodeByteArray(png,0,png.size))
        val markers=try {
            val pixels=IntArray(bitmap.width*bitmap.height)
            bitmap.getPixels(pixels,0,bitmap.width,0,0,bitmap.width,bitmap.height)
            val bgr=com.al1s.terminal.device.NativeFramePixels.bgr(pixels,bitmap.width,bitmap.height)
            val settings=JSONObject(config.toString())
            if(roi.size==4 && roi[2]>0 && roi[3]>0)settings.put("roi",org.json.JSONArray(roi))
            ColorMarkerDetector.find(bgr,bitmap.width,bitmap.height,settings)
        } finally { bitmap.recycle() }
        if(markers.isEmpty()) {
            misses[key]=(misses[key] ?: 0)+1
            events.onEvent("AL1S.ColorMarkers",JSONObject().put("name",node).put("matches",0).put("absence_checks",misses[key]).toString())
            return null
        }
        misses[key]=0
        val selected=markers[config.optInt("preferred_index",0).coerceIn(0,markers.size-1)]
        val detail=JSONObject().put("matches",markers.size).put("box",org.json.JSONArray(selected.box))
            .put("target",org.json.JSONArray(selected.target)).put("score",selected.score)
        events.onEvent("AL1S.ColorMarkers",JSONObject(detail.toString()).put("name",node).toString())
        return NativeRecognitionResult(true,intArrayOf(selected.target[0],selected.target[1],1,1),detail.toString())
    }
}
