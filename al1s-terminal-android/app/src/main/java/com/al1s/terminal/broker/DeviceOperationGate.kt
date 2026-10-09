package com.al1s.terminal.broker

import java.util.concurrent.locks.ReentrantReadWriteLock
import kotlin.concurrent.read
import kotlin.concurrent.write

/** Only replacement closes admission; existing calls finish before its decision. */
class DeviceOperationGate {
    private val lock = ReentrantReadWriteLock(true)
    private var closing = false
    fun <T> invoke(operation: () -> T): T = lock.read {
        check(!closing) { "helper_replacement_in_progress" }
        operation()
    }
    fun <T> exclusive(operation: () -> T): T = lock.write {
        check(!closing) { "helper_replacement_in_progress" }
        operation()
    }
    fun beginReplacement(canReplace: () -> Boolean): Boolean = lock.write {
        if (closing || !canReplace()) false else { closing = true; true }
    }
}
