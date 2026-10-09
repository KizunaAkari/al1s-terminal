package com.al1s.terminal.activation

import android.content.Context
import com.al1s.terminal.broker.BrokerClient
import java.util.concurrent.TimeUnit

class ActivationCoordinator(private val context: Context) {
    private val connector: LocalAdbConnector get() = synchronized(activationLock) {
        sharedConnector ?: LocalAdbConnector(context.applicationContext).also { sharedConnector = it }
    }

    fun pair(port: Int, code: String): Boolean = connector.pairLocal(port, code)

    fun activate(port: Int, allowed: () -> Boolean = { true }): Boolean = synchronized(activationLock) {
        check(allowed()) { "activation_cancelled" }
        if (BrokerClient.codeCurrent(context)) return true
        if (connectedPort != null && connectedPort != port) {
            runCatching { connector.disconnect() }
            connectedPort = null
        }
        check(connector.isConnected || connector.connectLocal(port)) { "Local ADB connection was not established" }
        connectedPort = port
        check(allowed()) { "activation_cancelled" }
        val nonce = BrokerClient.beginActivation()
        try { startBroker(nonce, allowed) } finally { BrokerClient.cancelActivation(nonce) }
    }

    private fun startBroker(nonce: String, allowed: () -> Boolean): Boolean {
        val apk = context.applicationInfo.sourceDir
        require(apk.startsWith("/data/app/") && !apk.contains("'") && !apk.contains("\n"))
        val command = "umask 077; CLASSPATH='$apk' /system/bin/setsid /system/bin/app_process / --nice-name=com.al1s.terminal.helper com.al1s.terminal.broker.BrokerMain '$nonce' >/data/local/tmp/al1s-broker-bootstrap.log 2>&1 </dev/null & wait"
        check(allowed()) { "activation_cancelled" }
        runCatching { bootstrapStream?.close() }
        bootstrapStream = connector.openStream("shell,raw:$command")
        var success = false
        try {
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
            while (!BrokerClient.activationReceived(nonce) && System.nanoTime() < deadline) {
                check(allowed()) { "activation_cancelled" }
                Thread.sleep(100)
            }
            check(BrokerClient.activationReceived(nonce) && BrokerClient.codeCurrent(context)) { "Local service handshake was not received" }
            check(BrokerClient.invoke("observe").getInt("uid") in setOf(0, 2000))
            runCatching {BrokerClient.invoke("provider_init")}
            success = true
            return true
        } finally {
            if (!success) {
                runCatching { bootstrapStream?.close() }
                bootstrapStream = null
                runCatching { connector.disconnect() }
                connectedPort = null
            }
        }
    }

    companion object {
        private val activationLock = Any()
        private var sharedConnector: LocalAdbConnector? = null
        private var bootstrapStream: io.github.muntashirakon.adb.AdbStream? = null
        private var connectedPort: Int? = null
    }
}
