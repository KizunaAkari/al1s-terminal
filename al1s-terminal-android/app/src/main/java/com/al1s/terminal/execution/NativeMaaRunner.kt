package com.al1s.terminal.execution

import android.content.Context
import com.al1s.terminal.device.*
import com.al1s.terminal.maa.*
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

data class PreparedMaaExecution(val body: JSONObject, val plan: AndroidMaaPlan,
    val modules: List<Pair<AndroidMaaModule, AndroidCompiledModule>>, val directory: File)
data class NativeExecutionResult(val result: String, val errorCode: String?, val diagnostic: JSONObject?)

class NativeMaaRunner(private val context: Context, private val libraries: String, private val workspace: File,
    private val ocr: OcrResources) {
    fun prepare(body: JSONObject, resources: Map<String, File>): PreparedMaaExecution {
        val plan = AndroidDefinitionLoader.load(body.getJSONObject("manifest"), resources)
        val id = java.util.UUID.fromString(body.getString("attempt_id")).toString()
        val directory = File(workspace, "attempt-$id").apply { check(isDirectory || mkdirs()) }
        CourseAssets.install(context.packageManager.getApplicationInfo("com.al1s.terminal",0).sourceDir,File(directory,"image"))
        val modules = AndroidPlanCompiler.compile(plan, File(directory, "image"))
        modules.forEach { (module, compiled) ->
            val handlers = NativeHandlers(MaaEventProjection(), pipeline=compiled.pipeline,
                device=NativeDeviceActions(context, module.target.optString("application_package").takeIf(String::isNotBlank)))
            HandlerRegistry.requireAll(compiled.pipeline, handlers.actionNames.toSet(), handlers.recognitionNames.toSet())
        }
        return PreparedMaaExecution(body, plan, modules, directory)
    }

    fun run(prepared: PreparedMaaExecution, cancelled: () -> Boolean,
        onEvent: (Int, String, JSONObject) -> Unit): NativeExecutionResult {
        val timeout = prepared.body.getInt("timeout_seconds")
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(timeout.toLong())
        val power = context.getSystemService(android.os.PowerManager::class.java)
        @Suppress("DEPRECATION")
        val wake = power.newWakeLock(android.os.PowerManager.SCREEN_BRIGHT_WAKE_LOCK, "AL1S:authorized-attempt")
        wake.acquire(TimeUnit.SECONDS.toMillis(timeout.toLong()) + 5000)
        var recording:com.al1s.terminal.media.NativeRecordingSession?=null
        var recordingError:String?=null
        val evidence=NativeEvidenceStore(NativeContextAccess)
        var outcome:NativeExecutionResult
        var media:JSONObject?=null
        try {
            check(!ApplicationLifecycle(context).settings().getBoolean("secure_keyguard")) { "unattended_requires_no_secure_keyguard" }
            ApplicationLifecycle(context).wake()
            NativeMaa.initialize(libraries)
            if(ExecutionMediaOptions.recordVideo(prepared.body)) {
                try {
                    val apk=context.packageManager.getApplicationInfo("com.al1s.terminal",0).sourceDir
                    recording=com.al1s.terminal.media.NativeRecordingSession(apk,File(prepared.directory,"recording.mp4")).also {it.start()}
                } catch(error:Exception) {recordingError=error.javaClass.simpleName}
            }
            outcome=executeModules(prepared,deadline,cancelled,onEvent,evidence)
        } catch (error: Exception) {
            outcome=NativeExecutionResult("failure", "maa_execution_failed", JSONObject().put("cause", error.javaClass.simpleName)
                .put("message", error.message?.take(256) ?: ""))
        } finally {
            recording?.let { session->
                val artifact=runCatching {session.finish()}.getOrElse {JSONObject().put("error",it.javaClass.simpleName)}
                media=artifact
                onEvent(-1,"AL1S.Recording",artifact)
            }
            recordingError?.let {media=JSONObject().put("error",it);onEvent(-1,"AL1S.Recording.Failed",JSONObject().put("cause",it))}
            if (wake.isHeld) wake.release()
        }
        return if(media==null)outcome else outcome.copy(diagnostic=(outcome.diagnostic ?: JSONObject()).put("recording",media))
    }

    private fun executeModules(prepared:PreparedMaaExecution,deadline:Long,cancelled:()->Boolean,
        onEvent:(Int,String,JSONObject)->Unit,evidence:NativeEvidenceStore):NativeExecutionResult {
        for((index,pair) in prepared.modules.withIndex()) {
            val (module,compiled)=pair
            if(cancelled())return NativeExecutionResult("cancelled","execution_cancelled",null)
            if(System.nanoTime()>=deadline)return NativeExecutionResult("failure","task_timeout",null)
            val result=try {runModule(prepared,module,compiled,index,deadline,cancelled,onEvent,evidence)}
                catch(error:Exception) {
                    NativeExecutionResult("failure","maa_execution_failed",MaaFailureDiagnostic.build(
                        module.scriptName,module.scriptVersionId,index,null,"runtime","maa_execution_failed")
                        .put("message",error.message?.take(256) ?: error.javaClass.simpleName))
                }
            if(result!=null)return result
            if(!delay(module.waitAfterMs,deadline,cancelled))return NativeExecutionResult(
                if(cancelled())"cancelled" else "failure",if(cancelled())"execution_cancelled" else "task_timeout",null)
        }
        return NativeExecutionResult("success",null,null)
    }

    private fun runModule(prepared: PreparedMaaExecution, module: AndroidMaaModule, compiled: AndroidCompiledModule,
        index: Int, deadline: Long, cancelled: () -> Boolean, onEvent: (Int, String, JSONObject) -> Unit,
        evidence:NativeEvidenceStore): NativeExecutionResult? {
        val packageName = module.target.optString("application_package").takeIf(String::isNotBlank)
        val device = NativeDeviceActions(context, packageName)
        val size = module.target.optJSONObject("screen_size")?.let { it.getInt("width") to it.getInt("height") } ?: currentSize()
        NativeMaa.bindDevice(NativeDeviceAdapter(context, size.first, size.second))
        var controller = 0L; var task = 0L
        val events = MaaEventProjection()
        val handlers = NativeHandlers(events, pipeline=compiled.pipeline, device=device,evidenceStore=evidence)
        val observe = events.observer
        events.observer = { message, details ->
            observe?.invoke(message, details)
            val value = JSONObject(details.toString()).put("script_name", module.scriptName).put("script_version_id", module.scriptVersionId)
                .put("module_index", index)
            onEvent(index, message, value)
        }
        try {
            controller = NativeMaa.connect("$libraries/libMaaFramework.so",
                NativeControllerConfig("$libraries/libal1s_maa.so", size.first, size.second).json())
            val pipelines = File(prepared.directory, "pipeline").apply { check(isDirectory || mkdirs()) }
            File(pipelines, "compiled.json").writeText(compiled.pipeline.toString())
            val requiresOcr = compiled.pipeline.keys().asSequence().any {
                compiled.pipeline.getJSONObject(it).optString("recognition") == "OCR"
            }
            task = NativeMaa.startTask(controller, prepared.directory.absolutePath, compiled.entry, compiled.pipeline.toString(),
                events, handlers, if (requiresOcr) ocr.path() else "")
            var status = NativeMaa.taskStatus(task)
            while (status in setOf(1000, 2000)) {
                if (cancelled() || System.nanoTime() >= deadline || handlers.budget.fatal || handlers.budget.clock.stalled()) {
                    NativeMaa.stopTask(task)
                    return NativeExecutionResult(if (cancelled()) "cancelled" else "failure", when {
                        cancelled() -> "execution_cancelled"
                        System.nanoTime() >= deadline -> "task_timeout"
                        else -> "maa_step_execution_stalled"
                    }, failureLocation(prepared, module, index, handlers, events,
                        if (cancelled()) "execution_cancelled" else if (System.nanoTime() >= deadline) "task_timeout" else "maa_step_execution_stalled", onEvent))
                }
                Thread.sleep(50)
                status = NativeMaa.taskStatus(task)
            }
            return if (status == 3000) null else NativeExecutionResult("failure", "maa_pipeline_failed",
                failureLocation(prepared, module, index, handlers, events, "maa_pipeline_failed", onEvent))
        } finally {
            if (task != 0L) { NativeMaa.stopTask(task); NativeMaa.destroyTask(task) }
            if (controller != 0L) NativeMaa.destroy(controller)
            if (module.cleanupOnFinish) device.cleanup()
        }
    }

    private fun currentSize(): Pair<Int, Int> {
        val display = checkNotNull(context.getSystemService(android.hardware.display.DisplayManager::class.java).getDisplay(0))
        val point = android.graphics.Point()
        @Suppress("DEPRECATION") display.getRealSize(point)
        return point.x to point.y
    }
    private fun location(module: AndroidMaaModule, index: Int, handlers: NativeHandlers, events: MaaEventProjection, code: String): JSONObject {
        val failure = handlers.failure.snapshot()
        val rule=handlers.failure.independentRuleFailure()
        val step = if(rule!=null)null else failure?.first ?: handlers.budget.clock.activeKey()?.substringAfterLast(':')?.toIntOrNull()
        val detail=MaaFailureDiagnostic.build(module.scriptName, module.scriptVersionId, index, step,
            if(rule!=null)"independent_rule" else failure?.second ?: "execution", code).put("native_events", JSONObject(events.snapshot()))
        val diagnosis=detail.getJSONArray("modules").getJSONObject(0).getJSONObject("result").getJSONObject("failure_diagnosis")
        if(rule!=null)diagnosis.put("title","独立规则执行失败").put("message","00 · 独立规则执行失败")
            .put("independent_rule",rule)
        handlers.failure.recognition.snapshot(step)?.let {recognition->
            diagnosis.put("recognition",recognition)
            val best=recognition.opt("actual_score") as? Number
            val threshold=recognition.opt("configured_threshold") as? Number
            if(best!=null && threshold!=null && diagnosis.optString("stage")=="recognition")diagnosis.put("message",
                "脚本“${module.scriptName}”第${checkNotNull(step)+1}步：连续${recognition.getInt("consecutive_misses")}次未命中，最高匹配%.4f，阈值%.4f".format(best.toDouble(),threshold.toDouble()))
        }
        return detail
    }
    private fun failureLocation(prepared: PreparedMaaExecution, module: AndroidMaaModule, index: Int,
        handlers: NativeHandlers, events: MaaEventProjection, code: String,
        onEvent: (Int,String,JSONObject) -> Unit): JSONObject {
        val detail = location(module,index,handlers,events,code)
        val result = detail.getJSONArray("modules").getJSONObject(0).getJSONObject("result")
        runCatching { FailureEvidence.capture(prepared.directory) }.onSuccess { capture ->
            result.put("failure_screenshot",capture)
            onEvent(index,"AL1S.Evidence",JSONObject().put("capture",capture).put("purpose","failure"))
        }.onFailure { result.put("failure_screenshot_error",it.javaClass.simpleName) }
        return detail
    }
    private fun delay(milliseconds: Int, deadline: Long, cancelled: () -> Boolean): Boolean {
        val end = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(milliseconds.toLong())
        while (System.nanoTime() < end) { if (cancelled() || System.nanoTime() >= deadline) return false; Thread.sleep(50) }
        return !cancelled() && System.nanoTime() < deadline
    }
}
