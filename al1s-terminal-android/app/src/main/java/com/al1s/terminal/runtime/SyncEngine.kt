package com.al1s.terminal.runtime

import android.content.Context
import com.al1s.terminal.data.TerminalDatabase
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.protocol.PlatformException
import com.al1s.terminal.security.IdentityStore
import com.al1s.terminal.security.TerminalConnectionGate
import kotlinx.coroutines.runBlocking

data class SyncResult(
    val online: Boolean,
    val commandsSeen: Int,
    val reportsSent: Int,
    val execution: String,
)

class SyncEngine(
    private val context: Context,
    private val identityStore: IdentityStore,
    private val database: TerminalDatabase,
) {
    fun runOnce(): SyncResult = TerminalConnectionGate.withConnection { runBlocking { cycle() } }

    private suspend fun cycle(): SyncResult {
        val identity = identityStore.load() ?: return SyncResult(false, 0, 0, "registration_required")
        val configuration = identityStore.configuration()
            ?: return SyncResult(false, 0, 0, "configuration_required")
        val platform = PlatformClient(configuration.first)
        val dispatcher = OutboxDispatcher(database, platform, identityStore)
        val execution = ExecutionCoordinator(database, dispatcher)
        execution.recoverInterrupted()

        var online = false
        var commandsSeen = 0
        var reportsSent = 0
        try {
            val rowVersion = platform.heartbeat(
                identity.credential,
                identity.terminalId,
                identity.terminalRowVersion,
            )
            identityStore.updateTerminalRowVersion(rowVersion)
            if (identity.capabilityRevision == 0) {
                val revision = platform.publishCapability(
                    identity.credential,
                    identity.terminalId,
                    1,
                    SystemCapability.read(context),
                )
                identityStore.updateCapabilityRevision(revision)
            }
            reportsSent += dispatcher.flush()
            val commands = platform.listCommands(identity.credential)
            commandsSeen = commands.size
            val receiver = PackageReceiver(database, platform, identity)
            for (command in commands) receiver.receive(command)
            reportsSent += dispatcher.flush()
            online = true
        } catch (error: PlatformException) {
            if (error.statusCode == 401) identityStore.clearCredential()
        }

        val executionResult = execution.executeNext()
        if (online) reportsSent += runCatching { dispatcher.flush() }.getOrDefault(0)
        return SyncResult(online, commandsSeen, reportsSent, executionResult)
    }

}
