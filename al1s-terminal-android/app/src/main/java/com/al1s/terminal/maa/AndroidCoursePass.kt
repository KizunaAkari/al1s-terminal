package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidCoursePass {
    val assets = linkedMapOf("location_select" to "location_select.png", "first_region" to "first_region_shale.png",
        "all_schedule" to "all_schedule.png", "start" to "start_schedule.png", "reward" to "reward.png",
        "confirm" to "confirm.png", "zero_ticket" to "zero_ticket_digits.png", "close" to "close.png",
        "affinity_popup" to "affinity_popup.png")

    fun apply(compiled: AndroidCompiledModule, script: JSONObject, imageDirectory:java.io.File?,materialize: (String)->String) {
        compiled.stepNodes.forEach { (index,name) ->
            val step=script.getJSONArray("steps").getJSONObject(index)
            if(step.getString("action")!="course_schedule")return@forEach
            val config=JSONObject().put("region_count",step.optInt("course_region_count",11))
                .put("ticket_limit",step.optInt("course_ticket_limit",7))
                .put("action_timeout_seconds",step.optDouble("course_action_timeout_seconds",25.0))
                .put("next_region_point",step.optJSONObject("course_next_region_point") ?: JSONObject().put("x",2260).put("y",545))
            require(config.getInt("region_count") in 1..20 && config.getInt("ticket_limit") in 1..20)
            require(config.getDouble("action_timeout_seconds") in 5.0..120.0)
            val sources=JSONObject()
            val size=script.optJSONObject("target")?.optJSONObject("screen_size")
            val width=size?.getInt("width") ?: 2400; val height=size?.getInt("height") ?: 1080
            fun roi(x:Int,y:Int,w:Int,h:Int)=JSONArray().put(x*width/2400).put(y*height/1080)
                .put(w*width/2400).put(h*height/1080)
            assets.forEach { (key,file) ->
                val source="${name}_Course_${key}Source"
                val hidden=match("course_$file",when(key){"zero_ticket"->0.95;"first_region","close","affinity_popup"->0.78;else->0.8})
                if(key=="zero_ticket")hidden.put("roi",roi(900,90,650,150))
                if(key=="close")hidden.put("roi",roi(1800,0,450,210))
                compiled.pipeline.put(source,hidden)
                sources.put(key,source)
            }
            val avatars=step.getJSONArray("course_target_avatars"); require(avatars.length() in 1..20)
            val values=JSONArray()
            for(i in 0 until avatars.length()) {
                val avatar=avatars.getJSONObject(i);val source="${name}_Course_Avatar_${i}Source"
                val threshold=avatar.optDouble("threshold",0.82);require(threshold.isFinite() && threshold in 0.1..1.0)
                val search=step.optJSONObject("course_avatar_search_roi") ?: JSONObject().put("x",300).put("y",180).put("width",1800).put("height",800)
                val file=java.io.File(checkNotNull(imageDirectory),materialize(avatar.getString("template_base64")))
                val scaled=CourseAvatarScale.scale(file,width,height,avatar.optInt("screen_width",2400),avatar.optInt("screen_height",1080))
                compiled.pipeline.put(source,match(scaled.name,threshold)
                    .put("roi",roi(search.getInt("x"),search.getInt("y"),search.getInt("width"),search.getInt("height"))))
                values.put(JSONObject().put("source",source).put("name",avatar.optString("name","目标学生 ${i+1}")))
            }
            val click="${name}_Course_ClickSource"
            compiled.pipeline.put(click,JSONObject().put("recognition","DirectHit").put("action","Click").put("target",true)
                .put("pre_delay",0).put("post_delay",0).put("next",JSONArray()))
            config.put("sources",sources).put("target_avatars",values).put("click_source",click)
            compiled.pipeline.getJSONObject(name).put("custom_action_param",config)
        }
    }

    private fun match(file:String,threshold:Double)=JSONObject().put("recognition","TemplateMatch")
        .put("template",JSONArray().put(file)).put("threshold",threshold).put("method",5).put("action","DoNothing")
        .put("next",JSONArray()).put("pre_delay",0).put("post_delay",0)
}
