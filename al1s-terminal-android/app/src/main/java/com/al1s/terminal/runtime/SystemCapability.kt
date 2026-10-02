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
            .put("provider_keys", PlatformClient.PROVIDER_KEYS)
            .put(
                "details",
                JSONObject()
                    .put("provider", "xiaomi-root-demo")
                    .put("manufacturer", Build.MANUFACTURER)
                    .put("model", Build.MODEL),
            )
    }
}
