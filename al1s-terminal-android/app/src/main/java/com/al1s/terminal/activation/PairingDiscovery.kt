package com.al1s.terminal.activation

import android.content.Context
import android.os.Handler
import android.os.Looper
import io.github.muntashirakon.adb.android.AdbMdns
import java.net.NetworkInterface
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Upstream discovery checks interface ownership; repeat that check before using only loopback. */
class PairingDiscovery(context: Context, pairing: Boolean, listener: (Int) -> Unit) : AutoCloseable {
    private val main = Handler(Looper.getMainLooper())
    private val mdns = AdbMdns(context.applicationContext,
        if (pairing) AdbMdns.SERVICE_TYPE_TLS_PAIRING else AdbMdns.SERVICE_TYPE_TLS_CONNECT) { address, port ->
        if (address != null && LocalAdbEndpoint.accepts("127.0.0.1", port)) {
            val local = Collections.list(NetworkInterface.getNetworkInterfaces()).any { network ->
                Collections.list(network.inetAddresses).any { it == address }
            }
            if (local) listener(port)
        }
    }

    fun start() { main.post { mdns.start() } }
    override fun close() { main.post { runCatching { mdns.stop() } } }

    companion object {
        fun connectionPort(context: Context): Int {
            check(Looper.myLooper() != Looper.getMainLooper())
            val found = CountDownLatch(1)
            var port = -1
            PairingDiscovery(context, false) { port = it; found.countDown() }.use {
                it.start()
                check(found.await(15, TimeUnit.SECONDS)) { "local_connection_port_not_found" }
            }
            return port
        }
    }
}
