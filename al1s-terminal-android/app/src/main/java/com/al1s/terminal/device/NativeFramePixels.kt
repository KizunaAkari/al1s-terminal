package com.al1s.terminal.device

/** Maa Android external ABI requires tightly packed CV_8UC3 BGR pixels. */
object NativeFramePixels {
    fun bgr(argb: IntArray, width: Int, height: Int): ByteArray {
        require(width in 1..16384 && height in 1..16384)
        val count = width.toLong() * height
        require(count * 3 <= 32 * 1024 * 1024 && count == argb.size.toLong())
        val output = ByteArray(argb.size * 3)
        argb.forEachIndexed { index, pixel ->
            output[index * 3] = pixel.toByte()
            output[index * 3 + 1] = (pixel ushr 8).toByte()
            output[index * 3 + 2] = (pixel ushr 16).toByte()
        }
        return output
    }
}
