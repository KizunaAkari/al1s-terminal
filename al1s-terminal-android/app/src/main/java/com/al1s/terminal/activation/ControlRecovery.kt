package com.al1s.terminal.activation

import android.content.Context
import com.al1s.terminal.broker.BrokerClient
import java.util.concurrent.atomic.AtomicBoolean

object ControlRecovery {
    private val active = AtomicBoolean(false)
    fun reconcile(context: Context, allowed: () -> Boolean) {
        if (BrokerClient.codeCurrent(context) || !allowed() || !PairingIdentityStore(context).hasIdentity() ||
            !active.compareAndSet(false, true)) return
        try {
            WirelessRecovery(context).restoreIfAllowed()
            val port = PairingDiscovery.connectionPort(context)
            ActivationCoordinator(context).activate(port, allowed)
        } finally { active.set(false) }
    }
}
