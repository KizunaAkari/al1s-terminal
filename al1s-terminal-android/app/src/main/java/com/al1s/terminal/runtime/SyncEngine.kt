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
    fun runOnce(): SyncResult = AgentReconciliationGate.tryRun {
        TerminalConnectionGate.withConnection { runBlocking { cycle() } }
    } ?: SyncResult(false, 0, 0, "reconciliation_busy")

    fun heartbeatOnly(): Boolean = TerminalConnectionGate.withConnection {
        val identity = identityStore.load() ?: return@withConnection false
        val configuration = identityStore.configuration() ?: return@withConnection false
        val platform=PlatformClient(configuration.first)
        try {
            if(identityStore.needsOwnedState())identityStore.updateOwnedState(platform.ownIdentity(identity.credential,identity.terminalId))
            val current=identityStore.load() ?: return@withConnection false
            val version = platform.heartbeat(
                current.credential, current.terminalId, current.terminalRowVersion,identityStore.acceptanceStatus(),
            )
            identityStore.updateTerminalRowVersion(version)
            true
        } catch (error: PlatformException) {
            if (error.statusCode == 401) identityStore.clearCredential()
            if(error.statusCode==409)runCatching {identityStore.updateOwnedState(platform.ownIdentity(identity.credential,identity.terminalId))}
            false
        }
    }

    private suspend fun cycle(): SyncResult {
        val identity = identityStore.load() ?: return SyncResult(false, 0, 0, "registration_required")
        val configuration = identityStore.configuration()
            ?: return SyncResult(false, 0, 0, "configuration_required")
        val platform = PlatformClient(configuration.first)
        val dispatcher = OutboxDispatcher(database, platform, identityStore)

        var online = false
        var commandsSeen = 0
        var reportsSent = 0
        try {
            val capability=SystemCapability.read(context)
            val fingerprint=CapabilityFingerprint.of(capability)
            if (identity.capabilityRevision == 0 || identityStore.capabilityFingerprint()!=fingerprint) {
                val revision = platform.publishCapability(
                    identity.credential,
                    identity.terminalId,
                    identity.capabilityRevision+1,
                    capability,
                )
                identityStore.confirmCapability(revision,fingerprint)
                identityStore.updateOwnedState(platform.ownIdentity(identity.credential,identity.terminalId))
            }
            reportsSent += dispatcher.flush()
            val commands = platform.listCommands(identity.credential)
            commandsSeen = commands.size
            val receiver = PackageReceiver(database, platform, identity, context)
            for (command in commands) receiver.receive(command)
            reportsSent += dispatcher.flush()
            online = true
        } catch (error: PlatformException) {
            if (error.statusCode == 401) identityStore.clearCredential()
        }

        return SyncResult(online, commandsSeen, reportsSent, "control_activation_required")
    }

}
