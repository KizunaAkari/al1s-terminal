package com.al1s.terminal.maa

import android.graphics.BitmapFactory
import java.io.File
import java.io.FileOutputStream
import kotlin.math.roundToInt

object CourseAvatarScale {
    fun scale(file:File,width:Int,height:Int,sourceWidth:Int,sourceHeight:Int):File {
        require(width>0 && height>0 && sourceWidth>0 && sourceHeight>0)
        val sx=width.toDouble()/sourceWidth;val sy=height.toDouble()/sourceHeight
        if(kotlin.math.abs(sx-1)<0.02 && kotlin.math.abs(sy-1)<0.02)return file
        val original=checkNotNull(BitmapFactory.decodeFile(file.absolutePath)) {"course_avatar_decode_failed"}
        try {
            val w=(original.width*sx).roundToInt().coerceAtLeast(1);val h=(original.height*sy).roundToInt().coerceAtLeast(1)
            require(w in 1..8192 && h in 1..8192 && w.toLong()*h<=16777216)
            val scaled=android.graphics.Bitmap.createScaledBitmap(original,w,h,true)
            try {
                val output=File(file.parentFile,"scaled_${width}_${height}_${file.name}")
                FileOutputStream(output).use {check(scaled.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it));it.fd.sync()}
                return output
            } finally {if(scaled!==original)scaled.recycle()}
        } finally {original.recycle()}
    }
}
