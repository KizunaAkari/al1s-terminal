package com.al1s.terminal.maa

interface MaaContextAccess {
    fun recognize(context: Long, source: String, image: Long): NativeRecognitionResult
    fun action(context: Long, source: String, box: IntArray, detail: String): Boolean
    fun capture(context: Long): Long
    fun imageSize(image: Long): IntArray
    fun encodedImage(image: Long): ByteArray = throw UnsupportedOperationException("image_encoding_unavailable")
    fun destroyImage(image: Long)
    fun nodeData(context: Long, source: String): String
    fun stopping(context: Long): Boolean
    fun runTask(context: Long, entry: String, pipeline: String): Boolean = throw UnsupportedOperationException("nested_task_unavailable")
    fun clearHitCount(context: Long, node: String): Boolean = throw UnsupportedOperationException("hit_count_unavailable")
}

object NativeContextAccess : MaaContextAccess {
    override fun recognize(context: Long, source: String, image: Long) = NativeContext.recognize(context, source, image)
    override fun action(context: Long, source: String, box: IntArray, detail: String) = NativeContext.action(context, source, box, detail)
    override fun capture(context: Long) = NativeContext.capture(context)
    override fun imageSize(image: Long) = NativeContext.imageSize(image)
    override fun encodedImage(image: Long) = NativeContext.encodedImage(image)
    override fun destroyImage(image: Long) = NativeContext.destroyImage(image)
    override fun nodeData(context: Long, source: String) = NativeContext.nodeData(context, source)
    override fun stopping(context: Long) = NativeContext.stopping(context)
    override fun runTask(context: Long, entry: String, pipeline: String) = NativeContext.runTask(context,entry,pipeline)
    override fun clearHitCount(context: Long, node: String) = NativeContext.clearHitCount(context,node)
}
