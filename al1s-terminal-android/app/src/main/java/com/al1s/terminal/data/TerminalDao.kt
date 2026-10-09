package com.al1s.terminal.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface TerminalDao {
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertQuickTest(value:QuickTestInboxEntity):Long
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertQuickResources(values:List<QuickTestResourceEntity>)
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertQuickEvents(values:List<QuickTestEventEntity>)
    @Query("SELECT * FROM quick_test_inbox WHERE terminalId=:terminal AND state!='completed' ORDER BY createdAt LIMIT 1")
    fun activeQuickTest(terminal:String):QuickTestInboxEntity?
    @Query("SELECT * FROM quick_test_inbox WHERE state='running' ORDER BY createdAt LIMIT 1")
    fun runningQuickTest():QuickTestInboxEntity?
    @Query("UPDATE quick_test_inbox SET state='running',updatedAt=:now WHERE sessionId=:id AND state='queued' AND NOT EXISTS (SELECT 1 FROM inbox_tasks WHERE state IN ('queued','running')) AND NOT EXISTS (SELECT 1 FROM quick_test_inbox WHERE state='running')")
    fun markQuickRunning(id:String,now:Long):Int
    @Query("SELECT * FROM quick_test_resources WHERE sessionId=:id ORDER BY resourceKey")
    fun quickResources(id:String):List<QuickTestResourceEntity>
    @Query("UPDATE quick_test_inbox SET state=:state,resultJson=:result,updatedAt=:now WHERE sessionId=:id")
    fun updateQuickTest(id:String,state:String,result:String?,now:Long):Int
    @Query("UPDATE quick_test_inbox SET lastHelperSequence=:helper,lastEventSequence=:event WHERE sessionId=:id")
    fun updateQuickCursor(id:String,helper:Long,event:Int):Int
    @Query("SELECT * FROM quick_test_events WHERE sessionId=:id AND confirmed=0 ORDER BY sequence LIMIT 50")
    fun pendingQuickEvents(id:String):List<QuickTestEventEntity>
    @Query("UPDATE quick_test_events SET confirmed=1 WHERE sessionId=:id AND sequence<=:sequence")
    fun confirmQuickEvents(id:String,sequence:Int):Int
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertSetupReport(value:SetupCheckReportEntity)
    @Query("SELECT * FROM setup_check_reports WHERE terminalId=:terminal AND confirmed=0 ORDER BY createdAt LIMIT 1")
    fun pendingSetupReport(terminal:String):SetupCheckReportEntity?
    @Query("UPDATE setup_check_reports SET confirmed=1 WHERE requestId=:id AND terminalId=:terminal")
    fun confirmSetupReport(id:String,terminal:String):Int
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertPackageResources(values: List<PackageResourceEntity>)
    @Insert(onConflict=OnConflictStrategy.IGNORE) fun insertMaaLink(value: MaaAttemptLinkEntity)
    @Query("SELECT * FROM package_resources WHERE packageId=:id ORDER BY resourceKey") fun packageResources(id: String): List<PackageResourceEntity>
    @Query("SELECT * FROM maa_attempt_links WHERE attemptId=:id") fun maaLink(id: String): MaaAttemptLinkEntity?
    @Query("UPDATE maa_attempt_links SET helperEpoch=:epoch,helperStatus=:status,updatedAt=:now WHERE attemptId=:id")
    fun updateMaaLink(id: String, epoch: String?, status: String, now: Long): Int
    @Query("UPDATE maa_attempt_links SET lastEventSequence=:sequence,updatedAt=:now WHERE attemptId=:id AND lastEventSequence<:sequence")
    fun confirmMaaEvents(id: String, sequence: Long, now: Long): Int
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
            "WHERE packageId = :packageId AND state = 'queued' " +
            "AND NOT EXISTS (SELECT 1 FROM inbox_tasks WHERE state = 'running') " +
            "AND NOT EXISTS (SELECT 1 FROM quick_test_inbox WHERE state = 'running')",
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
