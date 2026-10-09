package com.al1s.terminal.device

import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Fixed Android executables only. Arguments are passed without a shell. */
internal object DeviceCommand {
    private val readers = Executors.newSingleThreadExecutor()
    @Synchronized fun run(arguments: List<String>, limit: Int = 65536, timeoutMillis:Long=8000): ByteArray {
        require(timeoutMillis in 1..8000)
        val deadline=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(timeoutMillis)
        fun remaining():Long=(TimeUnit.NANOSECONDS.toMillis(deadline-System.nanoTime())).also {
            check(it>0) {"device_command_timeout"}
        }
        require(arguments.first() in setOf("/system/bin/screencap", "/system/bin/am", "/system/bin/input") ||
            arguments == listOf("/system/bin/pm", "grant", "com.al1s.terminal", "android.permission.WRITE_SECURE_SETTINGS") ||
            arguments == listOf("/system/bin/wm", "dismiss-keyguard"))
        val process = ProcessBuilder(arguments).redirectError(java.io.File("/dev/null")).start()
        val read = readers.submit<ByteArray> {
            process.inputStream.use { stream ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (true) {
                    val size = stream.read(buffer)
                    if (size < 0) break
                    check(output.size().toLong() + size <= limit) { "device_output_limit" }
                    output.write(buffer, 0, size)
                }
                output.toByteArray()
            }
        }
        try {
            check(process.waitFor(remaining(), TimeUnit.MILLISECONDS)) { "device_command_timeout" }
            check(process.exitValue() == 0) { "device_command_failed" }
            return read.get(remaining(), TimeUnit.MILLISECONDS)
        } finally {
            process.destroyForcibly()
            process.inputStream.close()
            read.cancel(true)
        }
    }
}
