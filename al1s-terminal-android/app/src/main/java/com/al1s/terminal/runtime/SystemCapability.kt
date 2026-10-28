package com.al1s.terminal.runtime

import android.app.ActivityManager
import android.content.Context
import android.os.Build
import android.os.storage.StorageManager
import com.al1s.terminal.protocol.PlatformClient
import org.json.JSONObject

object SystemCapability {
    fun read(context: Context): JSONObject {
        val manager = context.getSystemService(ActivityManager::class.java)
        val memory = ActivityManager.MemoryInfo().also(manager::getMemoryInfo)
        val storage = runCatching {
            context.getSystemService(StorageManager::class.java)
                .getAllocatableBytes(StorageManager.UUID_DEFAULT)
        }.getOrDefault(0)
        val observation = if (com.al1s.terminal.broker.BrokerClient.codeCurrent(context))
            runCatching { com.al1s.terminal.broker.BrokerClient.invoke("observe") }.getOrNull() else null
        return JSONObject()
            .put("schema_version", 1)
            .put("protocol_version", 1)
            .put("agent_version", PlatformClient.AGENT_VERSION)
            .put("os_name", "android")
            .put("os_version", Build.VERSION.RELEASE)
            .put("architecture", Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown")
            .put("cpu_cores", Runtime.getRuntime().availableProcessors())
            .put("memory_bytes", memory.totalMem)
            .put("storage_available_bytes", storage)
            .put("accelerator_type", JSONObject.NULL)
            .put("low_resource", memory.totalMem < 4L * 1024 * 1024 * 1024)
            .put("provider_keys",org.json.JSONArray(ProviderReadiness.keys(observation?.getString("maa_version"),
                observation?.getBoolean("native_ready")==true,observation?.getBoolean("ocr_ready")==true)))
            .put(
                "details",
                JSONObject()
                    .put("provider", "android-direct")
                    .put("control_ready", observation != null)
                    .put("native_ready", observation?.getBoolean("native_ready") == true)
                    .put("maa_version", observation?.getString("maa_version") ?: JSONObject.NULL)
                    .put("readiness_error",observation?.getString("readiness_error") ?: JSONObject.NULL)
                    .put("root_authorized", observation?.getInt("uid") == 0)
                    .put("manufacturer", Build.MANUFACTURER)
                    .put("model", Build.MODEL),
            )
    }
}
