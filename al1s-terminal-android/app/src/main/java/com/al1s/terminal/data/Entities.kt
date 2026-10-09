package com.al1s.terminal.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "inbox_tasks",
    indices = [
        Index(value = ["commandId"], unique = true),
        Index(value = ["state", "createdAt"]),
        Index(value = ["attemptId"]),
    ],
)
data class InboxTaskEntity(
    @PrimaryKey val packageId: String,
    val commandId: String,
    val attemptId: String,
    val packageHash: String,
    val bodyJson: String,
    val action: String,
    val state: String,
    val packageReceiptReportId: String,
    val startReportId: String,
    val resultReportId: String,
    val permitId: String? = null,
    val permitVersion: Int? = null,
    val permitToken: String? = null,
    val permitExpiresAt: String? = null,
    val leaseId: String? = null,
    val leaseVersion: Int? = null,
    val resultKind: String? = null,
    val errorCode: String? = null,
    val cancellationRequested: Boolean = false,
    val createdAt: Long,
    val updatedAt: Long,
)

@Entity(
    tableName = "outbox_reports",
    indices = [Index(value = ["status", "availableAt", "createdAt"])],
)
data class OutboxReportEntity(
    @PrimaryKey val reportId: String,
    val packageId: String,
    val attemptId: String,
    val commandId: String? = null,
    val kind: String,
    val payloadJson: String,
    val status: String = "pending",
    val attemptCount: Int = 0,
    val availableAt: Long,
    val lastErrorCode: String? = null,
    val createdAt: Long,
)

@Entity(tableName = "processed_commands")
data class ProcessedCommandEntity(
    @PrimaryKey val commandId: String,
    val kind: String,
    val outcome: String,
    val processedAt: Long,
)

object InboxState {
    const val RECEIVED = "received"
    const val QUEUED = "queued"
    const val RUNNING = "running"
    const val RESULT_PENDING = "result_pending"
    const val COMPLETED = "completed"
    const val CANCELLED = "cancelled"
    const val REJECTED = "rejected"
}

object ReportKind {
    const val PACKAGE_RECEIPT = "package_receipt"
    const val ATTEMPT_START = "attempt_start"
    const val ATTEMPT_RESULT = "attempt_result"
    const val CANCEL_ACK = "cancel_ack"
}
