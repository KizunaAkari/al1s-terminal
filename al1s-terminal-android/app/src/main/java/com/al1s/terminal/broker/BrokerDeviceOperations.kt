package com.al1s.terminal.broker

import android.os.Bundle

@androidx.annotation.RequiresApi(27)
class BrokerDeviceOperations(private val libraries: String, private val context: android.content.Context,
    private val workspace: com.al1s.terminal.execution.HelperWorkspace, private val epoch: String) {
    private val native = BrokerNativeOperations(libraries, context)
    private val lifecycle = com.al1s.terminal.device.ApplicationLifecycle(context)
    private val media = BrokerMediaOperations(context)
    private val gate = DeviceOperationGate()
    private val authority = ManualInputAuthority(epoch)
    init {workspace.beginEpoch(epoch)}
    private val execution = com.al1s.terminal.execution.HelperExecutionService(context, libraries, workspace, epoch,revokeManualInput={
        media.revokeInput()
        native.releaseController()
    },debugAllowed={authority.allowsInput()})

    fun beginReplacement(): Boolean = gate.beginReplacement { execution.canReplace() && native.canReplace() && media.canReplace() }
    fun close() { execution.close(); native.close(); media.close(); workspace.close() }

    fun invoke(operation: String, payload: Bundle): Bundle = if (operation in setOf("task_start","path_release","authority_update","editor_prepare"))
        gate.exclusive { route(operation, payload) } else gate.invoke { route(operation, payload) }

    private fun route(operation: String, payload: Bundle): Bundle {
        if (operation in setOf("native_connect", "verify_task", "verify_ocr", "provider_init", "editor_ocr", "media_control", "wake"))
            check(!execution.busy()) { "native_execution_owns_input" }
        if(operation in setOf("media_control","wake"))authority.requireInput()
        if(operation=="bind_identity")authority.bind()
        return when (operation) {
        "editor_prepare" -> {
            if(execution.busy())Bundle().apply {putBoolean("prepared",false);putBoolean("automation",true)}
            else if(!authority.allowsInput())Bundle().apply {putBoolean("prepared",false)}
            else lifecycle.wake {authority.requireInput()}.apply {putBoolean("prepared",true)}
        }
        "path_state" -> Bundle().apply {
            putBoolean("busy",execution.busy());putString("instance_id",epoch)
            putString("previous_instance_id",workspace.previousEpoch)
            putBoolean("control_ready",com.al1s.terminal.maa.NativeMaa.initialize(libraries).removePrefix("v")=="5.12.1")
        }
        "authority_update" -> {
            authority.update(payload.getString("instance_id").orEmpty(),payload.getLong("generation"),
                payload.getBoolean("input_granted"),payload.getLong("deadline"))
            if(payload.getBoolean("holder_confirmed"))workspace.confirmEpoch(epoch)
            Bundle()
        }
        "path_release" -> {
            check(!execution.busy()) {"native_execution_owns_input"}
            authority.revoke();media.closeSession();native.releaseController()
            Bundle().apply {putBoolean("released",true)}
        }
        "verify_recording" -> {
            check(!execution.busy()) {"native_execution_owns_input"}
            val apk=context.packageManager.getApplicationInfo("com.al1s.terminal",0).sourceDir
            val file=java.io.File(workspace.directory,"recording-probe-${java.util.UUID.randomUUID()}.mp4")
            val recording=com.al1s.terminal.media.NativeRecordingSession(apk,file)
            recording.start()
            Thread.sleep(3500)
            val result=recording.finish()
            check(result.has("size_bytes")) {"recording_picture_absent"}
            Bundle().apply {putLong("size_bytes",result.getLong("size_bytes"));putParcelable("artifact",
                android.os.ParcelFileDescriptor.open(file,android.os.ParcelFileDescriptor.MODE_READ_ONLY))}
        }
        "bind_identity", "resource_import", "task_prepare", "task_start", "task_status", "task_cancel", "task_events", "task_settle_unstarted",
        "task_artifacts", "artifact_open", "artifact_confirm" -> execution.invoke(operation, payload)
        "observe", "provider_init", "native_connect", "screenshot", "verify_task", "verify_ocr", "editor_ocr" -> native.invoke(operation, payload)
        "foreground" -> lifecycle.foreground()
        "editor_screenshot" -> BrokerFrames.readOnly(com.al1s.terminal.device.ScreenFrameCapture.capture(
            snapshot={budget->com.al1s.terminal.device.DeviceCommand.run(
                listOf("/system/bin/screencap","-p"),16*1024*1024,timeoutMillis=budget)},
            blank=com.al1s.terminal.device.ScreenFrameCapture::blackPng))
        "application_icon" -> BrokerFrames.readOnly(lifecycle.icon(payload.getString("package_name").orEmpty()))
        "settings_check" -> lifecycle.settings()
        "wake" -> lifecycle.wake {authority.requireInput()}
        "media_open", "media_renew", "media_control", "media_close" -> media.invoke(operation, payload)
        "grant_wireless_recovery" -> {
            com.al1s.terminal.device.DeviceCommand.run(listOf("/system/bin/pm", "grant", "com.al1s.terminal",
                "android.permission.WRITE_SECURE_SETTINGS"))
            val owner = context.packageManager.getApplicationInfo("com.al1s.terminal", 0)
            Bundle().apply { putBoolean("granted", context.checkPermission("android.permission.WRITE_SECURE_SETTINGS",
                -1, owner.uid) == android.content.pm.PackageManager.PERMISSION_GRANTED) }
        }
        else -> error("broker_operation_unsupported")
        }
    }
}
