package com.al1s.terminal.maa

import org.json.JSONObject
import kotlin.math.*

data class ColorMarker(val box: IntArray, val target: IntArray, val score: Double, val components: Set<Int>)
private data class ColorComponent(val id: Int,val x:Int,val y:Int,val width:Int,val height:Int,val area:Int,val cx:Double,val cy:Double)

object ColorMarkerDetector {
    fun find(bgr: ByteArray,width:Int,height:Int,config:JSONObject):List<ColorMarker> {
        require(width in 1..8192 && height in 1..8192 && width.toLong()*height*3 == bgr.size.toLong())
        val mask=threshold(bgr,width,height,config)
        val closed=morph(morph(mask,width,height,true),width,height,false)
        config.optJSONArray("roi")?.let { roi ->
            val left=roi.getInt(0).coerceIn(0,width-1);val top=roi.getInt(1).coerceIn(0,height-1)
            val right=(left+roi.getInt(2)).coerceIn(left+1,width);val bottom=(top+roi.getInt(3)).coerceIn(top+1,height)
            for(y in 0 until height)for(x in 0 until width)if(x !in left until right || y !in top until bottom)closed[y*width+x]=0
        }
        val parts=components(closed,width,height,config).sortedByDescending { it.area }.take(48)
        val distance=config.optDouble("group_distance",115.0)
        require(distance.isFinite() && distance in 20.0..500.0)
        val candidates=mutableListOf<ColorMarker>()
        for(a in parts.indices) for(b in a+1 until parts.size) for(c in b+1 until parts.size) {
            val group=listOf(parts[a],parts[b],parts[c])
            val spread=maxOf(hypot(group[0].cx-group[1].cx,group[0].cy-group[1].cy),
                hypot(group[0].cx-group[2].cx,group[0].cy-group[2].cy),hypot(group[1].cx-group[2].cx,group[1].cy-group[2].cy))
            if(spread>distance)continue
            val x=group.minOf { it.x };val y=group.minOf { it.y }
            val w=group.maxOf { it.x+it.width }-x;val h=group.maxOf { it.y+it.height }-y
            if(w !in 35..185 || h !in 55..205 || group.maxOf { it.cy }-group.minOf { it.cy }<28)continue
            val area=group.sumOf { it.area };val density=area.toDouble()/(w*h)
            if(density<0.12)continue
            val box=intArrayOf(x,y,w,h)
            candidates+=ColorMarker(box,target(box,width,height,config),area*density/(1+spread/distance),group.map { it.id }.toSet())
        }
        val selected=mutableListOf<ColorMarker>()
        for(candidate in candidates.sortedByDescending { it.score })
            if(selected.none { (it.components intersect candidate.components).size>=2 })selected+=candidate
        return when(config.optString("order_by","Vertical")) {
            "Horizontal" -> selected.sortedWith(compareBy<ColorMarker> { it.target[0] }.thenBy { it.target[1] })
            "Score" -> selected.sortedByDescending { it.score }
            else -> selected.sortedWith(compareBy<ColorMarker> { it.target[1] }.thenBy { it.target[0] })
        }
    }

    private fun threshold(bgr:ByteArray,width:Int,height:Int,config:JSONObject):ByteArray {
        val lower=config.optJSONArray("hsv_lower")?.let { IntArray(3) { i->it.getInt(i) } } ?: intArrayOf(10,160,200)
        val upper=config.optJSONArray("hsv_upper")?.let { IntArray(3) { i->it.getInt(i) } } ?: intArrayOf(40,255,255)
        require((0..2).all { lower[it] in 0..255 && upper[it] in lower[it]..255 })
        val mask=ByteArray(width*height)
        for(i in mask.indices) {
            val b=bgr[i*3].toInt() and 255;val g=bgr[i*3+1].toInt() and 255;val r=bgr[i*3+2].toInt() and 255
            val max=maxOf(r,g,b);val min=minOf(r,g,b);val delta=max-min
            val hue=if(delta==0)0.0 else when(max) { r->60.0*(g-b)/delta;g->120+60.0*(b-r)/delta;else->240+60.0*(r-g)/delta }
            val h=round((if(hue<0)hue+360 else hue)/2).toInt()
            val s=if(max==0)0 else round(255.0*delta/max).toInt()
            if(h in lower[0]..upper[0] && s in lower[1]..upper[1] && max in lower[2]..upper[2])mask[i]=1
        }
        return mask
    }

    private fun morph(mask:ByteArray,width:Int,height:Int,dilate:Boolean):ByteArray {
        val result=ByteArray(mask.size)
        for(y in 0 until height)for(x in 0 until width) {
            var hit=!dilate
            for(dy in -1..1)for(dx in -1..1) {
                val row=y+dy;val col=x+dx
                val value=if(row in 0 until height && col in 0 until width)mask[row*width+col].toInt()!=0 else !dilate
                hit=if(dilate)hit||value else hit&&value
            }
            if(hit)result[y*width+x]=1
        }
        return result
    }

    private fun components(mask:ByteArray,width:Int,height:Int,config:JSONObject):List<ColorComponent> {
        val minimum=config.optInt("component_min_area",250);val maximum=config.optInt("component_max_area",1800)
        require(minimum>0 && maximum>minimum)
        val queue=IntArray(mask.size);val result=mutableListOf<ColorComponent>();var id=0
        for(start in mask.indices) {
            if(mask[start].toInt()==0)continue
            var read=0;var write=1;queue[0]=start;mask[start]=0;id++
            var left=width;var top=height;var right=0;var bottom=0;var sumX=0L;var sumY=0L
            while(read<write) {
                val pixel=queue[read++];val x=pixel%width;val y=pixel/width
                left=min(left,x);top=min(top,y);right=max(right,x);bottom=max(bottom,y);sumX+=x;sumY+=y
                for(dy in -1..1)for(dx in -1..1) {
                    val row=y+dy;val col=x+dx
                    if(row !in 0 until height || col !in 0 until width)continue
                    val next=row*width+col
                    if(mask[next].toInt()!=0) {mask[next]=0;queue[write++]=next}
                }
            }
            val w=right-left+1;val h=bottom-top+1
            if(write in minimum..maximum && w in 18..110 && h in 8..75 && write.toDouble()/(w*h)>=0.3)
                result+=ColorComponent(id,left,top,w,h,write,sumX.toDouble()/write,sumY.toDouble()/write)
        }
        return result
    }

    private fun target(box:IntArray,width:Int,height:Int,config:JSONObject):IntArray {
        val x=box[0];val y=box[1];val w=box[2];val h=box[3]
        val anchor=when(config.optString("anchor","center")) {
            "top_left"->x to y;"top_right"->x+w-1 to y;"bottom_left"->x to y+h-1
            "bottom_right"->x+w-1 to y+h-1;else->x+(w-1)/2 to y+(h-1)/2
        }
        return intArrayOf((anchor.first+config.optInt("offset_x",0)).coerceIn(0,width-1),
            (anchor.second+config.optInt("offset_y",0)).coerceIn(0,height-1))
    }
}
