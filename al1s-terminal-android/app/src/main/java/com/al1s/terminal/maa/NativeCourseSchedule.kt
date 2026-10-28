package com.al1s.terminal.maa

import org.json.JSONObject

/** Same target pass, unused third-row fallback and verified zero-ticket completion as Linux. */
class NativeCourseSchedule(private val port:MaaContextAccess,private val events:MaaEventProjection,
    private val budget:NativeBudgetHandler) {
    fun run(context:Long,node:String,config:JSONObject):Boolean = Flow(context,node,config).run()

    private inner class Flow(val context:Long,val node:String,val config:JSONObject) {
        val sources=config.getJSONObject("sources")
        val regions=config.getInt("region_count")
        val limit=config.getInt("ticket_limit")
        val timeout=config.getDouble("action_timeout_seconds")
        val used=Array(regions) {mutableSetOf<Int>()}
        var executed=0
        var size=intArrayOf(2400,1080)

        fun run():Boolean {
            require(regions in 1..20 && limit in 1..20 && timeout in 5.0..120.0)
            enterFirst()
            for(phase in listOf("target","fallback")) {
                if(phase=="fallback")nextRegion()
                for(region in 0 until regions) {
                    openModal()
                    while(!zero()) {
                        check(executed<limit) {"course_ticket_limit_without_zero"}
                        val candidate=if(phase=="target")target(region) else listOf(6,7).firstOrNull {it !in used[region]}
                        if(candidate==null)break
                        execute(region,candidate,phase)
                    }
                    if(zero()) {closeModal();return true}
                    closeModal()
                    if(region+1<regions)nextRegion()
                }
            }
            error("course_tickets_not_zero_after_all_regions")
        }

        fun enterFirst() {
            val deadline=System.nanoTime()+(timeout*1e9).toLong()
            while(ready() && System.nanoTime()<deadline) {
                if(match("all_schedule")!=null)return
                match("first_region")?.let {tap(it.box);waitFor("all_schedule",timeout);return}
                pause(350)
            }
            error("course_first_region_not_found")
        }

        fun openModal() {
            if(match("close")!=null)return
            tap(waitFor("all_schedule",timeout).box)
            waitFor("close",timeout)
        }

        fun closeModal() {match("close")?.let {tap(it.box);pause(600)}}
        fun zero()=match("zero_ticket")!=null
        fun nextRegion() {
            val point=config.getJSONObject("next_region_point")
            tap(intArrayOf((point.getInt("x")*size[0]/2400.0).toInt(),(point.getInt("y")*size[1]/1080.0).toInt(),1,1))
            pause(800)
        }

        fun target(region:Int):Int? {
            val avatars=config.getJSONArray("target_avatars")
            val candidates=mutableListOf<Pair<Int,Double>>()
            for(i in 0 until avatars.length()) {
                val source=avatars.getJSONObject(i).getString("source")
                capture { image ->
                    val result=port.recognize(context,source,image)
                    if(result.hit) {
                        val detail=runCatching {JSONObject(result.detail)}.getOrDefault(JSONObject())
                        val matches=detail.optJSONArray("filtered") ?: detail.optJSONArray("all")
                        if(matches!=null) for(j in 0 until minOf(matches.length(),100)) {
                            val candidate=MatchOffsetSelector.select(result,j) ?: continue
                            CourseGeometry.block(candidate.box,size)?.takeIf {it !in used[region]}?.let {
                                candidates.add(it to JSONObject(candidate.detail).optDouble("score",0.0))
                            }
                        } else CourseGeometry.block(result.box,size)?.takeIf {it !in used[region]}?.let {candidates.add(it to 0.0)}
                    }
                }
            }
            return candidates.maxByOrNull {it.second}?.first
        }

        fun execute(region:Int,block:Int,phase:String) {
            tap(CourseGeometry.click(block,size))
            val start=runCatching {waitFor("start",4.0)}.getOrNull()
            used[region].add(block)
            if(start==null) {
                check(ready()) {"course_cancelled_or_budget_exhausted"}
                trace(region,block,phase,"unavailable");return
            }
            tap(start.box)
            waitFor("reward",timeout,true)
            tap(waitFor("confirm",timeout,true).box)
            waitFor("close",timeout,true)
            executed++;trace(region,block,phase,"executed")
        }

        fun trace(region:Int,block:Int,phase:String,status:String) {
            events.onEvent("AL1S.CourseSchedule",JSONObject().put("name",node).put("region",region+1)
                .put("block",block+1).put("phase",phase).put("status",status).put("tickets_used",executed).toString())
        }
        fun waitFor(key:String,seconds:Double,dismiss:Boolean=false):NativeRecognitionResult {
            val deadline=System.nanoTime()+(seconds*1e9).toLong()
            while(ready() && System.nanoTime()<deadline) {
                match(key)?.let {return it}
                if(dismiss && match("affinity_popup")!=null) {
                    tap(intArrayOf(1676*size[0]/2400,782*size[1]/1080,1,1));pause(600)
                } else pause(350)
            }
            error("course_wait_timeout:$key")
        }
        fun match(key:String):NativeRecognitionResult? = capture { image ->
            port.recognize(context,sources.getString(key),image).takeIf {it.hit}
        }
        fun <T> capture(action:(Long)->T):T {
            check(ready()) {"course_cancelled_or_budget_exhausted"}
            val image=port.capture(context);check(image!=0L)
            try {size=port.imageSize(image);require(size.size==2 && size.all {it>0});return action(image)}
            finally {port.destroyImage(image)}
        }
        fun tap(box:IntArray) {check(ready());check(port.action(context,config.getString("click_source"),box,"{}"))}
        fun ready()=!port.stopping(context) && !budget.fatal && budget.clock.remaining()>0
        fun pause(ms:Long) {
            val deadline=System.nanoTime()+ms*1_000_000
            while(System.nanoTime()<deadline) {check(ready());Thread.sleep(minOf(50L,ms))}
        }
    }
}
