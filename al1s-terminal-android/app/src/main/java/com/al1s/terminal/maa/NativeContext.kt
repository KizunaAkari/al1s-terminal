package com.al1s.terminal.maa

/** Borrowed context/image handles are valid only while the native callback runs. */
object NativeContext {
    external fun recognize(context: Long, source: String, image: Long): NativeRecognitionResult
    external fun action(context: Long, source: String, box: IntArray, detail: String): Boolean
    external fun capture(context: Long): Long
    external fun imageSize(image: Long): IntArray
    external fun encodedImage(image: Long): ByteArray
    external fun imageFromEncoded(bytes: ByteArray): Long
    external fun destroyImage(image: Long)
    external fun nodeData(context: Long, source: String): String
    external fun stopping(context: Long): Boolean
    external fun runTask(context: Long, entry: String, pipeline: String): Boolean
    external fun clearHitCount(context: Long, node: String): Boolean
}

class NativeRecognitionResult(val hit: Boolean, val box: IntArray, val detail: String)
