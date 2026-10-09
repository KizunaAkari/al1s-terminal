package com.al1s.terminal.broker

import android.net.LocalServerSocket
import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Bundle
import java.io.DataInputStream
import java.io.DataOutputStream

/** Abstract local socket transfers a one-time challenge only, never a control key. */
class HelperRebindServer(private val ownerUid: Int, private val apk: String, private val payload: Bundle,
    private val canReplace: () -> Boolean, private val replace: () -> Unit) {
    private val server = LocalServerSocket(name(ownerUid))

    fun start() {
        Thread({
            while (true) {
                val client = runCatching { server.accept() }.getOrNull() ?: break
                client.use { socket -> runCatching { serve(socket) } }
            }
        }, "al1s-helper-rebind").start()
    }

    private fun serve(socket: LocalSocket) {
        socket.soTimeout = 3000
        val input = DataInputStream(socket.inputStream)
        val output = DataOutputStream(socket.outputStream)
        val nonce = input.readUTF()
        val requestedApk = input.readUTF()
        check(HelperRebindRequest.accepts(socket.peerCredentials.uid, nonce, requestedApk))
        when {
            requestedApk == apk -> {
                output.writeUTF(if (BrokerHandoff.deliver(nonce, payload)) "accepted" else "rejected")
                output.flush()
            }
            canReplace() -> {
                output.writeUTF("replace"); output.flush()
                server.close()
                replace()
            }
            else -> { output.writeUTF("busy"); output.flush() }
        }
    }

    companion object {
        private fun name(uid: Int) = "com.al1s.terminal.helper.$uid.v1"

        /** Returns false only when no previous helper exists or an idle upgrade was settled. */
        fun rebindExisting(uid: Int, apk: String, nonce: String): Boolean {
            val client = LocalSocket()
            try {
                try { client.connect(LocalSocketAddress(name(uid), LocalSocketAddress.Namespace.ABSTRACT)) }
                catch (_: java.io.IOException) { return false }
                client.soTimeout = 3000
                DataOutputStream(client.outputStream).apply { writeUTF(nonce); writeUTF(apk); flush() }
                return when (DataInputStream(client.inputStream).readUTF()) {
                    "accepted" -> true
                    "replace" -> { Thread.sleep(300); false }
                    "busy" -> error("helper_upgrade_blocked_by_active_operation")
                    else -> error("helper_rebind_rejected")
                }
            } finally { client.close() }
        }
    }
}
