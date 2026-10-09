package com.al1s.terminal.maa

import org.json.JSONObject

data class NativeControllerConfig(val libraryPath: String, val width: Int, val height: Int) {
    init {
        require(libraryPath.startsWith("/data/app/") && libraryPath.endsWith(".so"))
        require(width in 1..16384 && height in 1..16384)
    }
    fun json(): String = JSONObject().put("library_path", libraryPath)
        .put("screen_resolution", JSONObject().put("width", width).put("height", height))
        .put("display_id", 0).put("force_stop", false).toString()
}
