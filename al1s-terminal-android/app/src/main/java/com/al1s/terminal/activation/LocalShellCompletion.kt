package com.al1s.terminal.activation

/** The fixed background launcher has no response body. ADB CLSE is normal completion, not read EOF. */
object LocalShellCompletion {
    fun awaitClosed(
        closed: () -> Boolean,
        allowed: () -> Boolean,
        nowNanos: () -> Long = System::nanoTime,
        pause: () -> Unit = { Thread.sleep(25) },
    ) {
        val deadline = nowNanos() + 3_000_000_000
        while (!closed()) {
            check(allowed()) { "activation_cancelled" }
            check(nowNanos() < deadline) { "local_launcher_timeout" }
            pause()
        }
        check(allowed()) { "activation_cancelled" }
    }
}
