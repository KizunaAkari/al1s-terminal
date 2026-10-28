package com.al1s.terminal.maa

import kotlin.math.roundToInt

object CourseGeometry {
    private val columns=arrayOf(330 to 900,900 to 1510,1510 to 2115)
    private val rows=arrayOf(190 to 480,480 to 750,750 to 1015)
    fun block(box:IntArray,size:IntArray):Int? {
        val x=(box[0]+box[2]/2.0)*2400/size[0];val y=(box[1]+box[3]/2.0)*1080/size[1]
        val column=columns.indexOfFirst {x>=it.first && x<it.second}; val row=rows.indexOfFirst {y>=it.first && y<it.second}
        if(column<0 || row<0)return null
        return (row*3+column).takeIf {it<=7}
    }
    fun click(block:Int,size:IntArray):IntArray {
        require(block in 0..7 && size.size==2 && size.all {it>0})
        val c=columns[block%3];val r=rows[block/3]
        return intArrayOf(((c.first+c.second)/2.0*size[0]/2400).roundToInt(),
            ((r.first+70.0)*size[1]/1080).roundToInt(),1,1)
    }
}
