package com.al1s.terminal.device

import android.content.Context
import org.json.JSONObject

interface DeviceAutomationActions {
    fun start(): JSONObject
    fun cleanup(): JSONObject
    fun launch(packageName:String,activity:String):JSONObject = error("activity_launch_unavailable")
}

class NativeDeviceActions(private val context: Context, private val targetPackage: String?) : DeviceAutomationActions {
    override fun launch(packageName:String,activity:String):JSONObject {
        require(packageName.matches(Regex("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+")))
        require(activity.matches(Regex("[A-Za-z_.$][A-Za-z0-9_.$]*")))
        val component=if(activity.startsWith('.')) packageName+activity else activity
        context.packageManager.getActivityInfo(android.content.ComponentName(packageName,component),0)
        DeviceCommand.run(listOf("/system/bin/am","start","-W","-n","$packageName/$component"))
        return JSONObject().put("component",component).put("started",true)
    }
    private val lifecycle = ApplicationLifecycle(context)
    override fun start(): JSONObject {
        check(!lifecycle.settings().getBoolean("secure_keyguard")) { "unattended_requires_no_secure_keyguard" }
        lifecycle.wake()
        DeviceCommand.run(listOf("/system/bin/input", "keyevent", "3"))
        return JSONObject().put("accepted", true).put("home", true)
    }
    override fun cleanup(): JSONObject {
        targetPackage?.let { packageName ->
            require(packageName.matches(Regex("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+")) && packageName != "com.al1s.terminal")
            DeviceCommand.run(listOf("/system/bin/am", "force-stop", packageName))
        }
        DeviceCommand.run(listOf("/system/bin/input", "keyevent", "3"))
        return JSONObject().put("home", true).put("force_stopped_package", targetPackage ?: JSONObject.NULL)
    }
}
