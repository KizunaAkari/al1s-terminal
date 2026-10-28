package com.al1s.terminal.device

import java.nio.ByteBuffer

/** Check the original-frame allocation bound before invoking Android's decoder. */
object OriginalPngGeometry {
    fun require(data:ByteArray):Pair<Int,Int> {
        kotlin.require(data.size in 33..(16*1024*1024)) {"original_png_size_invalid"}
        kotlin.require(data.copyOfRange(0,8).contentEquals(byteArrayOf(137.toByte(),80,78,71,13,10,26,10)) &&
            data.copyOfRange(12,16).contentEquals("IHDR".toByteArray(Charsets.US_ASCII))) {"original_frame_not_png"}
        val header=ByteBuffer.wrap(data)
        kotlin.require(header.getInt(8)==13) {"original_frame_not_png"}
        val width=header.getInt(16);val height=header.getInt(20)
        kotlin.require(width in 1..8192 && height in 1..8192 && width.toLong()*height<=16_777_216) {
            "original_png_geometry_invalid"
        }
        return width to height
    }
}
