package com.al1s.terminal.security

import java.util.concurrent.locks.ReentrantReadWriteLock
import kotlin.concurrent.withLock

interface RegistrationIdentityStore {
    fun installationId(): String
    fun saveRegistration(
        baseUrl: String,
        displayName: String,
        terminalId: String,
        targetDeviceId: String,
        credential: String,
        terminalRowVersion: Int,
    )
}

/** A sync cycle and a successful identity commit must observe the same connection. */
object TerminalConnectionGate {
    private val lock = ReentrantReadWriteLock(true)
    fun <T> withConnection(action: () -> T): T = lock.readLock().withLock(action)
    fun <T> commitConnection(action: () -> T): T = lock.writeLock().withLock(action)
}
