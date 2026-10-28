package com.al1s.terminal.maa

object NativeMaa {
    @Volatile private var loaded = false
    @Synchronized fun initialize(directory: String): String {
        require(directory.startsWith("/data/app/"))
        if (!loaded) {
            for (name in listOf("libc++_shared.so", "libonnxruntime.so", "libopencv_world4.so",
                    "libfastdeploy_ppocr.so", "libMaaUtils.so", "libMaaFramework.so", "libal1s_maa.so")) {
                System.load("$directory/$name")
            }
            loaded = true
        }
        return version("$directory/libMaaFramework.so")
    }
    private external fun version(library: String): String
    external fun bindDevice(adapter: com.al1s.terminal.device.NativeDeviceAdapter)
    external fun connect(library: String, config: String): Long
    external fun screenshot(handle: Long): ByteArray
    external fun input(handle: Long, operation: String, values: IntArray): Boolean
    external fun destroy(handle: Long)
    external fun startTask(controller: Long, resourceDirectory: String, entry: String, pipeline: String, sink: MaaEventProjection,
        handlers: NativeHandlers? = null, ocrPath: String = ""): Long
    external fun taskStatus(task: Long): Int
    external fun stopTask(task: Long)
    external fun destroyTask(task: Long)
}
