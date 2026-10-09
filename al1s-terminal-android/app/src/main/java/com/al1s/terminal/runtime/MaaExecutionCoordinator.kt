package com.al1s.terminal.runtime

import android.content.Context
import androidx.room.withTransaction
import com.al1s.terminal.data.*
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.security.IdentityStore
import com.al1s.terminal.security.TerminalConnectionGate
import org.json.JSONObject
import java.time.Instant
import java.util.concurrent.atomic.AtomicBoolean

class MaaExecutionCoordinator(private val context: Context, private val database: TerminalDatabase, private val identities: IdentityStore) {
    fun cycle(): String = TerminalConnectionGate.withConnection { ExecutionAdmission.withAdmission {
        kotlinx.coroutines.runBlocking { performCycle() } } }

    private suspend fun performCycle(): String {
        if (!active.compareAndSet(false, true)) return "execution_reconciliation_busy"
        try {
            if (!com.al1s.terminal.broker.BrokerClient.connected()) return "control_activation_required"
            val identity = identities.load() ?: return "registration_required"
            val client = BrokerExecutionClient(context, identity)
            client.bind()
            val running = database.terminalDao().runningInbox()
            if (running != null && running.action == "maa") return reconcile(running, client)
            if(!com.al1s.terminal.broker.BrokerClient.codeCurrent(context))return "helper_upgrade_required"
            val queued = database.terminalDao().nextQueuedInbox() ?: return "idle"
            if (queued.action != "maa") return "other_executor_required"
            val debug=database.terminalDao().runningQuickTest()
            if(debug!=null) {client.cancel(debug.sessionId);return "debug_stopping_for_formal"}
            val body = JSONObject(com.al1s.terminal.resources.CanonicalBodyStore(java.io.File(context.filesDir,"maa/bodies"))
                .read(queued.packageHash,queued.bodyJson))
            if (running != null) return "other_executor_running"
            if (body.getString("terminal_id") != identity.terminalId || body.getString("target_device_id") != identity.targetDeviceId)
                return "task_identity_changed"
            val expiry = runCatching { Instant.parse(queued.permitExpiresAt) }.getOrNull() ?: return "permit_missing"
            val existing=client.status(queued.attemptId)
            val absent=existing.getString("status")=="absent"
            if (absent && !expiry.isAfter(Instant.now())) {
                val configuration = identities.configuration() ?: return "configuration_required"
                OutboxDispatcher(database, PlatformClient(configuration.first), identities).settleExpiredPrestart(queued)
                return "permit_expired_settled"
            }
            val staged = if(absent)client.prepare(queued, database.terminalDao().packageResources(queued.packageId)) else existing
            if(decision(queued,staged)==AttemptReconciliation.Action.WAIT)return "native_state_unconfirmed"
            database.terminalDao().insertMaaLink(MaaAttemptLinkEntity(queued.attemptId, queued.packageId, staged.getString("owner_epoch"),
                staged.getString("status").orEmpty(), 0, System.currentTimeMillis()))
            if (!markRunning(queued)) return "task_changed_before_start"
            val current = checkNotNull(database.terminalDao().inbox(queued.packageId))
            return reconcile(current,client)
        } finally { active.set(false) }
    }

    private suspend fun reconcile(task: InboxTaskEntity, client: BrokerExecutionClient): String {
        val status = client.status(task.attemptId)
        val action=decision(task,status)
        if(action!=AttemptReconciliation.Action.WAIT)database.terminalDao().updateMaaLink(task.attemptId,
            status.getString("owner_epoch"),status.getString("status").orEmpty(),System.currentTimeMillis())
        return when (action) {
            AttemptReconciliation.Action.CANCEL -> { client.cancel(task.attemptId); "cancellation_sent" }
            AttemptReconciliation.Action.SETTLE_UNSTARTED -> {client.settleUnstarted(task.attemptId);"expired_unstarted_settled"}
            AttemptReconciliation.Action.START -> {
                if(!com.al1s.terminal.broker.BrokerClient.codeCurrent(context))"helper_upgrade_required"
                else {client.start(task.attemptId);"unstarted_attempt_resumed"}
            }
            AttemptReconciliation.Action.REPORT -> {
                val configuration=identities.configuration() ?: return "configuration_required"
                val identity=identities.load() ?: return "registration_required"
                val mapping=MaaArtifactDispatcher(com.al1s.terminal.protocol.PlatformArtifactClient(PlatformClient(configuration.first)),client)
                    .flush(identity.credential,task.attemptId)
                val detail=ArtifactReferences.confirmed(status.getString("diagnostic")?.let {JSONObject(it)} ?: JSONObject(),
                    org.json.JSONArray(client.artifacts(task.attemptId).getString("artifacts").orEmpty()),mapping)
                queueResult(task, status.getString("result") ?: "failure", status.getString("error_code"), detail.toString())
                "native_result_queued"
            }
            AttemptReconciliation.Action.OBSERVE -> "native_running"
            AttemptReconciliation.Action.WAIT -> "native_state_unconfirmed"
        }
    }

    private fun decision(task:InboxTaskEntity,status:android.os.Bundle):AttemptReconciliation.Action {
        val state=status.getString("status")
        val owner=status.getString("owner_epoch")
        val runtime=status.getString("runtime_epoch")
        val expected=database.terminalDao().maaLink(task.attemptId)?.helperEpoch
        val epochValid=HelperAttemptProof.valid(state,owner,runtime,expected,status.getBoolean("started"))
        return AttemptReconciliation.decide(state,status.getString("package_hash")==task.packageHash,
            task.cancellationRequested,runCatching {Instant.parse(task.permitExpiresAt).isAfter(Instant.now())}.getOrDefault(false),
            epochValid,status.getBoolean("started"))
    }

    private suspend fun markRunning(task: InboxTaskEntity): Boolean {
        var marked = false
        val payload = JSONObject().put("protocol_version", 1).put("report_id", task.startReportId).put("attempt_id", task.attemptId)
            .put("package_id", task.packageId).put("offline_permit_id", task.permitId).put("offline_permit_token", task.permitToken)
            .put("occurred_at", Instant.now().toString())
        database.withTransaction {
            val now = System.currentTimeMillis()
            if (database.terminalDao().markRunning(task.packageId, now) == 1) {
                database.terminalDao().insertOutbox(OutboxReportEntity(task.startReportId, task.packageId, task.attemptId,
                    kind=ReportKind.ATTEMPT_START, payloadJson=payload.toString(), availableAt=now, createdAt=now))
                marked = true
            }
        }
        return marked
    }

    private suspend fun queueResult(task: InboxTaskEntity, result: String, code: String?, diagnostic: String?) {
        val now = System.currentTimeMillis()
        val payload = JSONObject().put("protocol_version", 1).put("report_id", task.resultReportId).put("attempt_id", task.attemptId)
            .put("package_id", task.packageId).put("result", result).put("error_code", code ?: JSONObject.NULL).put("retryable", false)
            .put("occurred_at", Instant.now().toString()).put("diagnostic", diagnostic?.let { JSONObject(it) } ?: JSONObject.NULL)
        database.withTransaction {
            if (database.terminalDao().markResultPending(task.packageId, result, code, now) == 1)
                database.terminalDao().insertOutbox(OutboxReportEntity(task.resultReportId, task.packageId, task.attemptId,
                    kind=ReportKind.ATTEMPT_RESULT, payloadJson=payload.toString(), availableAt=now, createdAt=now))
        }
    }
    companion object { private val active = AtomicBoolean(false) }
}
