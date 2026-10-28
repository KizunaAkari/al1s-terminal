package com.al1s.terminal.runtime

import java.util.concurrent.atomic.AtomicBoolean

/** All service, Worker and UI instances share queue ownership; heartbeats do not enter this gate. */
object AgentReconciliationGate {
    private val occupied = AtomicBoolean(false)

    fun <T : Any> tryRun(action: () -> T): T? {
        if (!occupied.compareAndSet(false, true)) return null
        return try { action() } finally { occupied.set(false) }
    }
}
