package com.al1s.terminal.execution

import org.json.JSONObject
import java.util.UUID

class HelperJournalRepository(private val database: HelperJournal, private val epoch: String,
    private val bodies: com.al1s.terminal.resources.CanonicalBodyStore) {
    private val dao = database.dao()
    fun recoverDeadOwner() { dao.settleInterrupted(epoch, System.currentTimeMillis()) }
    fun lookup(id: String) = dao.attempt(id)
    fun running() = dao.running()
    fun events(id: String, after: Long) = dao.events(id, after, 100)
    fun artifacts(id:String)=dao.artifacts(id)
    fun artifact(id:String)=dao.artifact(id)
    fun confirmArtifact(id:String,remote:String)=dao.confirmArtifact(id,remote)
    fun captureArtifact(attempt:String,message:String,event:JSONObject) {
        val capture=if(message=="AL1S.Evidence")event.optJSONObject("capture") else if(message=="AL1S.Recording")event else null
        if(capture==null || !capture.has("sha256"))return
        val id=capture.optString("artifact_id").takeIf {it.isNotEmpty()} ?: UUID.nameUUIDFromBytes("$attempt:recording".toByteArray()).toString()
        dao.insertArtifact(HelperArtifactEntity(id,attempt,if(message=="AL1S.Recording")"recording" else "screenshot",
            capture.getString("path"),capture.getString("sha256"),capture.getLong("size_bytes"),capture.getString("mime"),false,System.currentTimeMillis()))
    }

    fun accept(request: AuthorizedAttempt, body: String): HelperAttemptEntity {
        bodies.write(request.packageHash,body)
        database.runInTransaction {
            val old = dao.attempt(request.attemptId)
            if (old != null) {
                require(old.packageId == request.packageId && old.packageHash == request.packageHash &&
                    old.terminalId == request.terminalId && old.deviceId == request.deviceId) { "attempt_identity_conflict" }
            } else dao.insertAttempt(HelperAttemptEntity(request.attemptId, request.packageId, request.packageHash,
                request.terminalId, request.deviceId, request.permitId, request.permitExpiresAt, request.timeoutSeconds,
                "", "accepted", null, false, null, null, null, System.currentTimeMillis(), null, null,request.ownerKind))
        }
        return checkNotNull(dao.attempt(request.attemptId))
    }
    fun body(task:HelperAttemptEntity) = bodies.read(task.packageHash,task.bodyJson)
    fun start(id: String): Boolean {
        var started = false
        database.runInTransaction {
            if (dao.running() == null) started = dao.markRunning(id, epoch, System.currentTimeMillis()) == 1
        }
        return started
    }
    fun cancel(id: String) { database.runInTransaction { dao.cancelBeforeStart(id, System.currentTimeMillis()); dao.requestCancellation(id) } }
    fun settleExpiredUnstarted(id:String)=dao.settleExpiredUnstarted(id,System.currentTimeMillis())
    fun complete(id: String, result: String, code: String?, diagnostic: JSONObject?) {
        dao.complete(id, result, code, diagnostic?.toString(), System.currentTimeMillis())
    }
    fun append(id: String, sequence: Long, message: String, payload: JSONObject, module: Int?, step: Int?) {
        if(sequence>10000)return
        val original=payload.toString()
        val text = if(sequence==10000L || original.length>32768) JSONObject().put("name",payload.optString("name"))
            .put("truncated",true).put("original_message",message.take(160)).toString() else original
        val eventId = UUID.nameUUIDFromBytes("$id:$sequence".toByteArray(Charsets.UTF_8)).toString()
        dao.insertEvents(listOf(HelperEventEntity(id, sequence, eventId, message.take(160), module, step, text, System.currentTimeMillis())))
    }
}
