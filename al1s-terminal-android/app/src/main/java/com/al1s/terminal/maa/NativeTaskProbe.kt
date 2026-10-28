package com.al1s.terminal.maa

import android.os.Bundle
import java.io.File
import java.nio.file.Files
import java.util.concurrent.TimeUnit

/** Explicit local feasibility probe: real Tasker, no phone input or platform attempt. */
object NativeTaskProbe {
    fun verify(controller: Long, ocrPath: String = ""): Bundle {
        require(controller != 0L)
        val pipeline = org.json.JSONObject("""{"Entry":{"recognition":"DirectHit","action":"DoNothing","next":[],"pre_delay":0,"post_delay":0}}""")
        HandlerRegistry.requireAll(pipeline)
        val directory = Files.createTempDirectory(File("/data/local/tmp").toPath(), "al1s-maa-probe-").toFile()
        val folder = File(directory, "pipeline").apply { check(mkdir()) }
        val file = File(folder, "compiled.json").apply { writeText(pipeline.toString()) }
        val events = MaaEventProjection()
        val handlers = NativeHandlers(events)
        // Exercise a real Java custom-action callback without injecting phone input.
        pipeline.getJSONObject("Entry").put("action", "Custom").put("custom_action", "MaaProjectRandomWait")
            .put("custom_action_param", org.json.JSONObject().put("min_seconds", 0.05).put("max_seconds", 0.05))
        if (ocrPath.isNotEmpty()) pipeline.getJSONObject("Entry").put("recognition", "OCR")
            .put("expected", org.json.JSONArray().put("本机配对与激活")).put("roi", org.json.JSONArray().put(0).put(0).put(1080).put(600))
        HandlerRegistry.requireAll(pipeline, handlers.actionNames.toSet(), handlers.recognitionNames.toSet())
        file.writeText(pipeline.toString())
        var task = 0L
        try {
            task = NativeMaa.startTask(controller, directory.absolutePath, "Entry", pipeline.toString(), events, handlers, ocrPath)
            check(task != 0L) { "native_task_not_created" }
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
            var status = NativeMaa.taskStatus(task)
            while (status in setOf(1000, 2000) && System.nanoTime() < deadline) {
                Thread.sleep(50)
                status = NativeMaa.taskStatus(task)
            }
            check(status == 3000) { "native_task_probe_failed" }
            return Bundle().apply { putBoolean("success", true); putString("events", events.snapshot()) }
        } finally {
            if (task != 0L) { NativeMaa.stopTask(task); NativeMaa.destroyTask(task) }
            file.delete(); folder.delete(); directory.delete()
        }
    }
}
