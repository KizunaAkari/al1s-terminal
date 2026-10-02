package com.al1s.terminal.runtime

import java.util.concurrent.TimeUnit

data class DemoExecutionResult(val success: Boolean, val errorCode: String? = null)

fun interface FixedProcessRunner {
    fun run(command: List<String>): Int
}

class SystemProcessRunner : FixedProcessRunner {
    override fun run(command: List<String>): Int {
        val process = ProcessBuilder(command).redirectErrorStream(true).start()
        val finished = process.waitFor(10, TimeUnit.SECONDS)
        if (!finished) {
            process.destroyForcibly()
            return TIMEOUT_EXIT_CODE
        }
        process.inputStream.bufferedReader().use { it.readText() }
        return process.exitValue()
    }

    companion object {
        const val TIMEOUT_EXIT_CODE = -1
    }
}

class RootDemoExecutor(private val runner: FixedProcessRunner = SystemProcessRunner()) {
    fun execute(action: String): DemoExecutionResult {
        val command = ACTIONS[action]
            ?: return DemoExecutionResult(false, "android_demo_action_unsupported")
        val exitCode = runCatching { runner.run(command) }
            .getOrElse { return DemoExecutionResult(false, "root_provider_unavailable") }
        return when (exitCode) {
            0 -> DemoExecutionResult(true)
            SystemProcessRunner.TIMEOUT_EXIT_CODE -> DemoExecutionResult(false, "root_probe_timed_out")
            else -> DemoExecutionResult(false, "root_permission_denied")
        }
    }

    companion object {
        private val ACTIONS = mapOf("root_probe" to listOf("su", "-c", "id"))

        fun supports(action: String): Boolean = action in ACTIONS
    }
}
