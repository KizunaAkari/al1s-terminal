package com.al1s.terminal.maa

import android.os.Bundle
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files
import java.util.concurrent.TimeUnit

/** Read-only OCR of the user's PNG selection; no controller input is dispatched. */
object NativeCropOcr {
    fun read(controller:Long,ocrPath:String,crop:ByteArray):Bundle {
        require(crop.size in 33..(4*1024*1024))
        val dimension=java.nio.ByteBuffer.wrap(crop)
        val width=dimension.getInt(16);val height=dimension.getInt(20)
        require(width in 1..8192 && height in 1..8192 && width.toLong()*height<=4*1024*1024)
        val pipeline=JSONObject("""{"Entry":{"recognition":"DirectHit","action":"Custom","custom_action":"Al1sCropOCR","next":[],"pre_delay":0,"post_delay":0},"CropSource":{"recognition":"OCR","expected":[],"action":"DoNothing","next":[],"pre_delay":0,"post_delay":0}}""")
        val events=MaaEventProjection()
        var texts=JSONArray()
        val handlers=object:NativeHandlers(events) {
            override val actionNames=arrayOf("Al1sCropOCR")
            override fun act(context:Long,task:Long,node:String,name:String,parameters:String,recognition:Long,box:IntArray):Boolean {
                if(name!="Al1sCropOCR")return false
                val image=NativeContext.imageFromEncoded(crop);if(image==0L)return false
                try {
                    val result=NativeContext.recognize(context,"CropSource",image)
                    val all=JSONObject(result.detail.ifBlank {"{}"}).optJSONArray("all") ?: JSONArray()
                    texts=JSONArray((0 until minOf(all.length(),200)).mapNotNull {
                        all.optJSONObject(it)?.optString("text")?.take(1000)?.takeIf {text->text.isNotBlank()}
                    })
                    return true
                } finally {NativeContext.destroyImage(image)}
            }
        }
        val root=Files.createTempDirectory(File("/data/local/tmp").toPath(),"al1s-crop-ocr-").toFile()
        val folder=File(root,"pipeline").apply {check(mkdir())}
        val file=File(folder,"compiled.json").apply {writeText(pipeline.toString())}
        var task=0L
        try {
            task=NativeMaa.startTask(controller,root.absolutePath,"Entry",pipeline.toString(),events,handlers,ocrPath)
            val deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(25)
            var status=NativeMaa.taskStatus(task)
            while(status in setOf(1000,2000) && System.nanoTime()<deadline) {Thread.sleep(50);status=NativeMaa.taskStatus(task)}
            check(status==3000) {"crop_ocr_failed"}
            return Bundle().apply {putString("result",JSONObject().put("texts",texts).toString())}
        } finally {
            if(task!=0L) {NativeMaa.stopTask(task);NativeMaa.destroyTask(task)}
            file.delete();folder.delete();root.delete()
        }
    }
}
