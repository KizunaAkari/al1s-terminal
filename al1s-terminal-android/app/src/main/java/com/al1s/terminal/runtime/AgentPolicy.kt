package com.al1s.terminal.runtime

object AgentPolicy {
    fun shouldStart(userEnabled: Boolean, identityPresent: Boolean, storageReady: Boolean): Boolean =
        userEnabled && identityPresent && storageReady
}

data class ProviderObservation(
    val connected: Boolean,
    val nativeReady: Boolean,
    val rootAuthorized: Boolean,
) {
    val canExecute: Boolean get() = connected && nativeReady
    val isRoot: Boolean get() = connected && rootAuthorized
}

class AgentRetryPolicy {
    private var failures = 0

    @Synchronized fun failureDelaySeconds(): Long {
        failures = (failures + 1).coerceAtMost(6)
        return (1L shl failures).coerceAtMost(60)
    }

    @Synchronized fun connected() { failures = 0 }

    @Synchronized fun nextDelaySeconds(): Long =
        if (failures == 0) 15 else (1L shl failures).coerceAtMost(60)

    @Synchronized fun jitteredDelaySeconds(randomFraction: Double = kotlin.random.Random.nextDouble()): Long {
        require(randomFraction.isFinite() && randomFraction in 0.0..1.0)
        val base = nextDelaySeconds()
        return if (failures == 0) base else (base * (0.8 + 0.2 * randomFraction)).toLong().coerceAtLeast(1)
    }
}
