package com.al1s.terminal.runtime

import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/** Serialize formal/debug preparation and admission; the helper also enforces one native run. */
object ExecutionAdmission {
    private val lock=ReentrantLock(true)
    fun <T> withAdmission(action:()->T):T=lock.withLock(action)
}
