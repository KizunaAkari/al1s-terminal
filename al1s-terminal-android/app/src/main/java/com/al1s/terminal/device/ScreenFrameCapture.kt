package com.al1s.terminal.device

/** Wait briefly for the first composed image; never inject another input. */
object ScreenFrameCapture {
    fun capture(snapshot:(Long)->ByteArray,blank:(ByteArray)->Boolean,
        now:()->Long={System.nanoTime()/1_000_000},pause:()->Unit={Thread.sleep(100)}):ByteArray {
        val budget=now()+8000
        var frame=snapshot(8000)
        val grace=minOf(budget,now()+2000)
        while(blank(frame) && now()<grace) {
            pause()
            val remaining=budget-now()
            if(remaining<=0)break
            frame=snapshot(remaining)
        }
        return frame
    }

    fun blackPng(data:ByteArray):Boolean {
        val expected=OriginalPngGeometry.require(data)
        val image=checkNotNull(android.graphics.BitmapFactory.decodeByteArray(data,0,data.size)) {"screen_frame_decode_failed"}
        return try {
            check(image.width==expected.first && image.height==expected.second) {"original_png_geometry_changed"}
            val row=IntArray(image.width)
            for(y in 0 until image.height) {
                image.getPixels(row,0,image.width,0,y,image.width,1)
                if(row.any {it and 0x00ffffff != 0})return false
            }
            true
        } finally {image.recycle()}
    }
}
