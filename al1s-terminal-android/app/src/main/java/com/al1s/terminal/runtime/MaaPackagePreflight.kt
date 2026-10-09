package com.al1s.terminal.runtime

import android.content.Context
import com.al1s.terminal.device.NativeDeviceActions
import com.al1s.terminal.maa.*
import com.al1s.terminal.protocol.PlatformBlobClient
import com.al1s.terminal.protocol.TaskPackage
import com.al1s.terminal.resources.*
import com.al1s.terminal.security.IdentityStore
import com.al1s.terminal.security.TerminalIdentity
import java.io.File

class MaaPackagePreflight(private val context: Context) {
    fun receive(task: TaskPackage, validated: ValidatedPackage, identity: TerminalIdentity) {
        val configuration = IdentityStore(context).configuration() ?: error("configuration_required")
        val cache = VerifiedResourceStore(File(context.filesDir, "maa/resources"))
        val files = MaaPackageResources(cache, PlatformBlobClient(configuration.first)).receive(identity.credential, validated.resources)
        val plan = AndroidDefinitionLoader.load(task.body.getJSONObject("manifest"), files)
        val folder = File(context.filesDir, "maa/preflight/${java.util.UUID.fromString(task.packageId)}")
        CourseAssets.install(context.packageManager.getApplicationInfo("com.al1s.terminal",0).sourceDir,File(folder,"image"))
        AndroidPlanCompiler.compile(plan, File(folder, "image")).forEach { (module, compiled) ->
            val handlers = NativeHandlers(MaaEventProjection(), pipeline=compiled.pipeline,
                device=NativeDeviceActions(context, module.target.optString("application_package").takeIf(String::isNotBlank)))
            HandlerRegistry.requireAll(compiled.pipeline, handlers.actionNames.toSet(), handlers.recognitionNames.toSet())
        }
    }
}
