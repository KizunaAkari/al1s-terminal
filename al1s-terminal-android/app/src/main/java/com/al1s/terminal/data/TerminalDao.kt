package com.al1s.terminal.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface TerminalDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    fun insertInbox(task: InboxTaskEntity): Long

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    fun insertOutbox(report: OutboxReportEntity): Long

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    fun insertProcessedCommand(command: ProcessedCommandEntity): Long

    @Query("SELECT * FROM inbox_tasks WHERE packageId = :packageId")
    fun inbox(packageId: String): InboxTaskEntity?

    @Query("SELECT * FROM inbox_tasks WHERE attemptId = :attemptId LIMIT 1")
    fun inboxByAttempt(attemptId: String): InboxTaskEntity?

    @Query("SELECT * FROM inbox_tasks WHERE state = 'running' ORDER BY createdAt LIMIT 1")
    fun runningInbox(): InboxTaskEntity?

    @Query("SELECT * FROM inbox_tasks WHERE state = 'queued' ORDER BY createdAt LIMIT 1")
    fun nextQueuedInbox(): InboxTaskEntity?

    @Query("SELECT * FROM processed_commands WHERE commandId = :commandId")
    fun processedCommand(commandId: String): ProcessedCommandEntity?

    @Query(
        "SELECT * FROM outbox_reports " +
            "WHERE status = 'pending' AND availableAt <= :now " +
            "ORDER BY createdAt LIMIT :limit",
    )
    fun pendingReports(now: Long, limit: Int): List<OutboxReportEntity>

    @Query(
        "UPDATE inbox_tasks SET permitId = :permitId, permitVersion = :permitVersion, " +
            "permitToken = :permitToken, permitExpiresAt = :expiresAt, state = 'queued', " +
            "updatedAt = :now WHERE packageId = :packageId AND state = 'received'",
    )
    fun acceptPackage(
        packageId: String,
        permitId: String,
        permitVersion: Int,
        permitToken: String,
        expiresAt: String,
        now: Long,
    ): Int

    @Query(
        "UPDATE inbox_tasks SET state = 'running', updatedAt = :now " +
            "WHERE packageId = :packageId AND state = 'queued'",
    )
    fun markRunning(packageId: String, now: Long): Int

    @Query(
        "UPDATE inbox_tasks SET leaseId = :leaseId, leaseVersion = :leaseVersion, " +
            "updatedAt = :now WHERE packageId = :packageId",
    )
    fun attachLease(packageId: String, leaseId: String, leaseVersion: Int, now: Long): Int

    @Query(
        "UPDATE inbox_tasks SET state = 'result_pending', resultKind = :resultKind, " +
            "errorCode = :errorCode, updatedAt = :now " +
            "WHERE packageId = :packageId AND state = 'running'",
    )
    fun markResultPending(
        packageId: String,
        resultKind: String,
        errorCode: String?,
        now: Long,
    ): Int

    @Query(
        "UPDATE inbox_tasks SET state = 'completed', updatedAt = :now " +
            "WHERE packageId = :packageId AND state = 'result_pending'",
    )
    fun markCompleted(packageId: String, now: Long): Int

    @Query(
        "UPDATE inbox_tasks SET state = 'completed', resultKind = 'failure', " +
            "errorCode = 'offline_permit_expired', updatedAt = :now " +
            "WHERE packageId = :packageId AND state = 'queued'",
    )
    fun markExpiredSettled(packageId: String, now: Long): Int

    @Query(
        "UPDATE inbox_tasks SET state = 'cancelled', resultKind = 'cancelled', " +
            "errorCode = 'task_cancelled', updatedAt = :now " +
            "WHERE packageId = :packageId AND state = 'queued'",
    )
    fun markCancelledPrestart(packageId: String, now: Long): Int

    @Query(
        "UPDATE inbox_tasks SET cancellationRequested = 1, updatedAt = :now " +
            "WHERE attemptId = :attemptId AND state IN ('received', 'queued', 'running')",
    )
    fun requestCancellation(attemptId: String, now: Long): Int

    @Query(
        "UPDATE inbox_tasks SET state = 'cancelled', resultKind = 'cancelled', " +
            "errorCode = 'cancelled_before_start', updatedAt = :now " +
            "WHERE attemptId = :attemptId AND state IN ('received', 'queued')",
    )
    fun cancelBeforeStart(attemptId: String, now: Long): Int

    @Query("DELETE FROM outbox_reports WHERE reportId = :reportId")
    fun confirmReport(reportId: String): Int

    @Query(
        "UPDATE outbox_reports SET attemptCount = attemptCount + 1, availableAt = :availableAt, " +
            "lastErrorCode = :errorCode WHERE reportId = :reportId",
    )
    fun deferReport(reportId: String, availableAt: Long, errorCode: String): Int
}
