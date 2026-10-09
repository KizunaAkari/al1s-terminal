package com.al1s.terminal.runtime

import androidx.room.withTransaction
import com.al1s.terminal.data.InboxState
import com.al1s.terminal.data.InboxTaskEntity
import com.al1s.terminal.data.OutboxReportEntity
import com.al1s.terminal.data.ReportKind
import com.al1s.terminal.data.TerminalDatabase
import org.json.JSONObject
import java.time.Instant

class ExecutionCoordinator(
    private val database: TerminalDatabase,
    private val dispatcher: OutboxDispatcher,
    private val executor: RootDemoExecutor = RootDemoExecutor(),
) {
    suspend fun recoverInterrupted(): Boolean {
        val task = database.terminalDao().runningInbox() ?: return false
        if(task.action=="maa")return false
        queueResult(task, "failure", "execution_interrupted")
        return true
    }

    suspend fun executeNext(): String {
        val dao = database.terminalDao()
        val task = dao.nextQueuedInbox() ?: return "idle"
        if(task.action=="maa")return "native_executor_required"
        val permitId = task.permitId ?: return "permit_missing"
        val permitToken = task.permitToken ?: return "permit_missing"
        val expiresAt = runCatching { task.permitExpiresAt?.let(Instant::parse) }.getOrNull()
            ?: return "permit_invalid"
        if (!expiresAt.isAfter(Instant.now())) {
            return try {
                dispatcher.settleExpiredPrestart(task)
                "permit_expired_settled"
            } catch (error: com.al1s.terminal.protocol.PlatformException) {
                "permit_expired_pending"
            }
        }
        val now = System.currentTimeMillis()
        val startPayload = JSONObject()
            .put("protocol_version", 1)
            .put("report_id", task.startReportId)
            .put("attempt_id", task.attemptId)
            .put("package_id", task.packageId)
            .put("offline_permit_id", permitId)
            .put("offline_permit_token", permitToken)
            .put("occurred_at", Instant.now().toString())
        var marked=false
        database.withTransaction {
            if (dao.markRunning(task.packageId, now) != 1) return@withTransaction
            marked=true
            dao.insertOutbox(
                OutboxReportEntity(
                    reportId = task.startReportId,
                    packageId = task.packageId,
                    attemptId = task.attemptId,
                    kind = ReportKind.ATTEMPT_START,
                    payloadJson = startPayload.toString(),
                    availableAt = now,
                    createdAt = now,
                ),
            )
        }
        if(!marked)return "executor_busy"
        runCatching { dispatcher.flush() }
        val running = dao.inbox(task.packageId) ?: return "missing_after_start"
        val result = if (running.cancellationRequested) {
            DemoExecutionResult(false, "execution_cancelled")
        } else {
            executor.execute(running.action)
        }
        queueResult(
            running,
            if (result.success) "success" else if (result.errorCode == "execution_cancelled") {
                "cancelled"
            } else {
                "failure"
            },
            result.errorCode,
        )
        return if (result.success) "success" else requireNotNull(result.errorCode)
    }

    private suspend fun queueResult(task: InboxTaskEntity, result: String, errorCode: String?) {
        val now = System.currentTimeMillis()
        val payload = JSONObject()
            .put("protocol_version", 1)
            .put("report_id", task.resultReportId)
            .put("attempt_id", task.attemptId)
            .put("package_id", task.packageId)
            .put("result", result)
            .put("error_code", errorCode ?: JSONObject.NULL)
            .put("retryable", false)
            .put("occurred_at", Instant.now().toString())
        database.withTransaction {
            if (database.terminalDao().markResultPending(
                    task.packageId,
                    result,
                    errorCode,
                    now,
                ) == 1
            ) {
                database.terminalDao().insertOutbox(
                    OutboxReportEntity(
                        reportId = task.resultReportId,
                        packageId = task.packageId,
                        attemptId = task.attemptId,
                        kind = ReportKind.ATTEMPT_RESULT,
                        payloadJson = payload.toString(),
                        availableAt = now,
                        createdAt = now,
                    ),
                )
            }
        }
    }
}
