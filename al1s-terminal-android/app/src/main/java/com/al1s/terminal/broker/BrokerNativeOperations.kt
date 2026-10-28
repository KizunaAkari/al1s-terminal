package com.al1s.terminal.broker

import android.content.Context
import android.os.Bundle
import com.al1s.terminal.device.NativeDeviceAdapter
import com.al1s.terminal.maa.NativeControllerConfig
import com.al1s.terminal.maa.NativeMaa
import com.al1s.terminal.maa.NativeTaskProbe

@androidx.annotation.RequiresApi(27)
class BrokerNativeOperations(private val libraries: String, private val context: Context) : AutoCloseable {
    private var controller = 0L
    private var version: String? = null
    private var initialized=false
    private var taskReady=false
    private var ocrReady=false
    private var readinessError:String?=null
    private val ocr = com.al1s.terminal.maa.OcrResources(context.packageManager.getApplicationInfo("com.al1s.terminal", 0).sourceDir)

    @Synchronized fun canReplace() = true // Probe calls retain this monitor until their native task has joined.
    @Synchronized fun releaseController() { if (controller != 0L) NativeMaa.destroy(controller); controller = 0L }
    @Synchronized fun invoke(operation: String, payload: Bundle): Bundle = when (operation) {
        "observe" -> Bundle().apply { putBoolean("native_ready",taskReady);putBoolean("ocr_ready",ocrReady);putString("maa_version",version);putString("readiness_error",readinessError) }
        "provider_init" -> {
            if(!initialized) {
                initialized=true
                val display=checkNotNull(context.getSystemService(android.hardware.display.DisplayManager::class.java).getDisplay(0))
                val size=android.graphics.Point()
                @Suppress("DEPRECATION") display.getRealSize(size)
                var stage="native_controller"
                try {
                    invoke("native_connect",Bundle().apply {putInt("width",size.x);putInt("height",size.y)})
                    stage="native_task"
                    taskReady=NativeTaskProbe.verify(controller).getBoolean("success")
                } catch(error:Exception) {
                    readinessError="$stage:${error.javaClass.simpleName}"
                    android.util.Log.w("AL1S-Readiness",checkNotNull(readinessError))
                    throw error
                }
                runCatching {
                    com.al1s.terminal.maa.NativeCropOcr.read(controller,ocr.path(),NativeMaa.screenshot(controller))
                }.onSuccess {ocrReady=true}.onFailure {readinessError="ocr:${it.javaClass.simpleName}";android.util.Log.w("AL1S-Readiness",checkNotNull(readinessError))}
            }
            invoke("observe",Bundle())
        }
        "native_connect" -> {
            version = NativeMaa.initialize(libraries)
            if (controller != 0L) NativeMaa.destroy(controller)
            controller = 0L
            NativeMaa.bindDevice(NativeDeviceAdapter(context, payload.getInt("width"), payload.getInt("height")))
            val config = NativeControllerConfig("$libraries/libal1s_maa.so", payload.getInt("width"), payload.getInt("height"))
            controller = NativeMaa.connect("$libraries/libMaaFramework.so", config.json())
            check(controller != 0L)
            Bundle().apply { putBoolean("native_ready", true); putString("maa_version", version) }
        }
        "screenshot" -> {
            check(controller != 0L) { "native_controller_required" }
            BrokerFrames.readOnly(NativeMaa.screenshot(controller))
        }
        "verify_task" -> NativeTaskProbe.verify(controller).also {taskReady=it.getBoolean("success")}
        "verify_ocr" -> NativeTaskProbe.verify(controller, ocr.path()).also {ocrReady=it.getBoolean("success")}
        "editor_ocr" -> {
            if(controller==0L) {
                val display=checkNotNull(context.getSystemService(android.hardware.display.DisplayManager::class.java).getDisplay(0))
                val size=android.graphics.Point()
                @Suppress("DEPRECATION") display.getRealSize(size)
                invoke("native_connect",Bundle().apply {putInt("width",size.x);putInt("height",size.y)})
            }
            @Suppress("DEPRECATION") val memory=checkNotNull(payload.getParcelable<android.os.SharedMemory>("image"))
            val crop=try {
                val buffer=memory.mapReadOnly()
                try {require(buffer.remaining() in 33..(4*1024*1024));ByteArray(buffer.remaining()).also {buffer.get(it)}}
                finally {android.os.SharedMemory.unmap(buffer)}
            } finally {memory.close()}
            com.al1s.terminal.maa.NativeCropOcr.read(controller,ocr.path(),crop)
        }
        else -> error("unsupported_native_operation")
    }
    @Synchronized override fun close() { if (controller != 0L) NativeMaa.destroy(controller); controller = 0L; ocr.close() }
}
