package com.al1s.terminal.runtime

import androidx.room.withTransaction
import com.al1s.terminal.data.InboxState
import com.al1s.terminal.data.InboxTaskEntity
import com.al1s.terminal.data.OutboxReportEntity
import com.al1s.terminal.data.ReportKind
import com.al1s.terminal.data.TerminalDatabase
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.protocol.PlatformException
import com.al1s.terminal.protocol.PrestartSettlement
import com.al1s.terminal.security.IdentityStore
import org.json.JSONObject
import java.time.Instant

class OutboxDispatcher(
    private val database: TerminalDatabase,
    private val platform: PlatformClient,
    private val identityStore: IdentityStore,
) {
    suspend fun settleExpiredPrestart(task: InboxTaskEntity) {
        val identity = identityStore.load() ?: throw PlatformException(
            401,
            "registration_required",
            "Terminal registration is required",
        )
        val permitId = requireNotNull(task.permitId)
        val report = JSONObject()
            .put("protocol_version", 1)
            .put("report_id", task.resultReportId)
            .put("attempt_id", task.attemptId)
            .put("package_id", task.packageId)
            .put("offline_permit_id", permitId)
            .put("occurred_at", Instant.now().toString())
        val settlement = try {
            platform.settleExpiredPrestart(identity.credential, report)
        } catch (error: PlatformException) {
            if (error.statusCode == 401) identityStore.clearCredential()
            throw error
        }
        database.withTransaction {
            val now = System.currentTimeMillis()
            when (settlement) {
                PrestartSettlement.EXPIRED_FAILURE -> database.terminalDao().markExpiredSettled(task.packageId, now)
                PrestartSettlement.ALREADY_CANCELLED -> database.terminalDao().markCancelledPrestart(task.packageId, now)
            }
        }
    }

    suspend fun flush(limit: Int = 50): Int {
        val identity = identityStore.load() ?: return 0
        val reports = database.terminalDao().pendingReports(System.currentTimeMillis(), limit)
        var confirmed = 0
        for (report in reports) {
            try {
                if (send(report, identity.credential)) confirmed += 1
            } catch (error: PlatformException) {
                if (error.statusCode == 401) {
                    identityStore.clearCredential()
                    throw error
                }
                defer(report, error.code)
            } catch (error: RuntimeException) {
                defer(report, error::class.java.simpleName)
            }
        }
        return confirmed
    }

    private suspend fun send(report: OutboxReportEntity, credential: String): Boolean {
        val dao = database.terminalDao()
        val payload = JSONObject(report.payloadJson)
        when (report.kind) {
            ReportKind.PACKAGE_RECEIPT -> {
                val permit = platform.sendPackageReceipt(credential, payload)
                database.withTransaction {
                    if (permit != null) {
                        dao.acceptPackage(
                            report.packageId,
                            permit.permitId,
                            permit.permitVersion,
                            permit.token,
                            permit.expiresAt,
                            System.currentTimeMillis(),
                        )
                    }
                    dao.confirmReport(report.reportId)
                }
            }
            ReportKind.ATTEMPT_START -> {
                val lease = platform.startAttempt(credential, payload)
                database.withTransaction {
                    dao.attachLease(
                        report.packageId,
                        lease.leaseId,
                        lease.leaseVersion,
                        System.currentTimeMillis(),
                    )
                    dao.confirmReport(report.reportId)
                }
            }
            ReportKind.ATTEMPT_RESULT -> {
                val task = dao.inbox(report.packageId) ?: return false
                if (task.state != InboxState.RESULT_PENDING) {
                    dao.confirmReport(report.reportId)
                    return true
                }
                val leaseId = task.leaseId ?: return false
                val leaseVersion = task.leaseVersion ?: return false
                platform.sendAttemptResult(
                    credential,
                    JSONObject(payload.toString())
                        .put("lease_id", leaseId)
                        .put("lease_version", leaseVersion),
                )
                database.withTransaction {
                    dao.markCompleted(report.packageId, System.currentTimeMillis())
                    dao.confirmReport(report.reportId)
                }
            }
            ReportKind.CANCEL_ACK -> {
                platform.acknowledgeCancellation(credential, payload)
                dao.confirmReport(report.reportId)
            }
            else -> error("Unsupported outbox report kind ${report.kind}")
        }
        return true
    }

    private fun defer(report: OutboxReportEntity, errorCode: String) {
        val delaySeconds = minOf(300L, 1L shl minOf(report.attemptCount + 1, 8))
        database.terminalDao().deferReport(
            report.reportId,
            System.currentTimeMillis() + delaySeconds * 1000,
            errorCode.take(100),
        )
    }
}
