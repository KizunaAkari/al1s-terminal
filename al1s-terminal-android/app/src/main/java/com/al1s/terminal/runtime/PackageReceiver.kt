package com.al1s.terminal.runtime

import androidx.room.withTransaction
import com.al1s.terminal.data.InboxState
import com.al1s.terminal.data.InboxTaskEntity
import com.al1s.terminal.data.OutboxReportEntity
import com.al1s.terminal.data.ProcessedCommandEntity
import com.al1s.terminal.data.ReportKind
import com.al1s.terminal.data.TerminalDatabase
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.protocol.TerminalCommand
import com.al1s.terminal.security.TerminalIdentity
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

class PackageReceiver(
    private val database: TerminalDatabase,
    private val platform: PlatformClient,
    private val identity: TerminalIdentity,
) {
    suspend fun receive(command: TerminalCommand) {
        when (command.kind) {
            "task_package_available" -> receivePackage(command)
            "cancel_requested" -> receiveCancellation(command)
        }
    }

    private suspend fun receivePackage(command: TerminalCommand) {
        val packageId = command.packageId ?: return
        val dao = database.terminalDao()
        if (dao.inbox(packageId) != null || dao.processedCommand(command.commandId) != null) return
        val taskPackage = platform.getTaskPackage(identity.credential, packageId)
        val validated = runCatching { PackageValidator.validate(taskPackage, identity) }
        val error = validated.exceptionOrNull() as? PackageValidationException
        if (error != null) {
            queuePackageReceipt(command, packageId, "rejected", error.code, error.message)
            return
        }
        val now = System.currentTimeMillis()
        val task = InboxTaskEntity(
            packageId = packageId,
            commandId = command.commandId,
            attemptId = command.attemptId,
            packageHash = taskPackage.packageHash,
            bodyJson = taskPackage.body.toString(),
            action = requireNotNull(validated.getOrNull()).action,
            state = InboxState.RECEIVED,
            packageReceiptReportId = UUID.randomUUID().toString(),
            startReportId = UUID.randomUUID().toString(),
            resultReportId = UUID.randomUUID().toString(),
            createdAt = now,
            updatedAt = now,
        )
        database.withTransaction {
            if (database.terminalDao().insertInbox(task) >= 0) {
                database.terminalDao().insertOutbox(acceptedReceipt(task, now))
            }
        }
    }

    private suspend fun receiveCancellation(command: TerminalCommand) {
        val dao = database.terminalDao()
        if (dao.processedCommand(command.commandId) != null) return
        val task = dao.inboxByAttempt(command.attemptId)
        val now = System.currentTimeMillis()
        val outcome = when (task?.state) {
            InboxState.RECEIVED, InboxState.QUEUED -> "cancelled_before_start"
            InboxState.RUNNING -> "running_cancel_accepted"
            else -> "already_completed"
        }
        val reportId = UUID.randomUUID().toString()
        val payload = JSONObject()
            .put("protocol_version", 1)
            .put("report_id", reportId)
            .put("command_id", command.commandId)
            .put("outcome", outcome)
            .put("occurred_at", Instant.now().toString())
        database.withTransaction {
            if (outcome == "cancelled_before_start") {
                dao.cancelBeforeStart(command.attemptId, now)
            } else if (outcome == "running_cancel_accepted") {
                dao.requestCancellation(command.attemptId, now)
            }
            dao.insertProcessedCommand(
                ProcessedCommandEntity(command.commandId, command.kind, outcome, now),
            )
            dao.insertOutbox(
                OutboxReportEntity(
                    reportId = reportId,
                    packageId = task?.packageId.orEmpty(),
                    attemptId = command.attemptId,
                    commandId = command.commandId,
                    kind = ReportKind.CANCEL_ACK,
                    payloadJson = payload.toString(),
                    availableAt = now,
                    createdAt = now,
                ),
            )
        }
    }

    private suspend fun queuePackageReceipt(
        command: TerminalCommand,
        packageId: String,
        disposition: String,
        rejectionCode: String,
        diagnostic: String?,
    ) {
        val now = System.currentTimeMillis()
        val reportId = UUID.randomUUID().toString()
        val payload = receiptPayload(
            reportId,
            command,
            packageId,
            disposition,
            rejectionCode,
            diagnostic,
        )
        database.withTransaction {
            database.terminalDao().insertProcessedCommand(
                ProcessedCommandEntity(
                    command.commandId,
                    command.kind,
                    "rejected:$rejectionCode",
                    now,
                ),
            )
            database.terminalDao().insertOutbox(
                OutboxReportEntity(
                    reportId = reportId,
                    packageId = packageId,
                    attemptId = command.attemptId,
                    commandId = command.commandId,
                    kind = ReportKind.PACKAGE_RECEIPT,
                    payloadJson = payload.toString(),
                    availableAt = now,
                    createdAt = now,
                ),
            )
        }
    }

    private fun acceptedReceipt(task: InboxTaskEntity, now: Long): OutboxReportEntity {
        val command = TerminalCommand(task.commandId, "task_package_available", task.packageId, task.attemptId)
        return OutboxReportEntity(
            reportId = task.packageReceiptReportId,
            packageId = task.packageId,
            attemptId = task.attemptId,
            commandId = task.commandId,
            kind = ReportKind.PACKAGE_RECEIPT,
            payloadJson = receiptPayload(
                task.packageReceiptReportId,
                command,
                task.packageId,
                "accepted",
                null,
                null,
            ).toString(),
            availableAt = now,
            createdAt = now,
        )
    }

    private fun receiptPayload(
        reportId: String,
        command: TerminalCommand,
        packageId: String,
        disposition: String,
        rejectionCode: String?,
        diagnostic: String?,
    ): JSONObject = JSONObject()
        .put("protocol_version", 1)
        .put("report_id", reportId)
        .put("package_id", packageId)
        .put("command_id", command.commandId)
        .put("attempt_id", command.attemptId)
        .put("disposition", disposition)
        .put("rejection_code", rejectionCode ?: JSONObject.NULL)
        .put("diagnostic", diagnostic?.take(512) ?: JSONObject.NULL)
        .put("occurred_at", Instant.now().toString())
}
