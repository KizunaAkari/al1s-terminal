package com.al1s.terminal.runtime

import android.content.Context
import androidx.room.withTransaction
import com.al1s.terminal.broker.BrokerClient
import com.al1s.terminal.data.*
import com.al1s.terminal.protocol.*
import com.al1s.terminal.resources.CanonicalBodyStore
import com.al1s.terminal.security.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant

class QuickTestCoordinator(private val context:Context,private val database:TerminalDatabase,
    private val identities:IdentityStore) {
    @Synchronized fun cycle()=TerminalConnectionGate.withConnection {ExecutionAdmission.withAdmission {runBlocking {cycleOnce()}}}
    private suspend fun cycleOnce() {
        val identity=identities.load() ?: return
        val origin=identities.configuration()?.first ?: return
        val platform=PlatformClient(origin);val client=BrokerExecutionClient(context,identity)
        val dao=database.terminalDao()
        var entry=dao.activeQuickTest(identity.terminalId)
        if(entry?.state=="result_pending" && QuickPreflightResult.isLocalFailure(JSONObject(checkNotNull(entry.resultJson)))) {
            reportResult(entry,client,platform,identity);return
        }
        if(!BrokerClient.connected())return
        client.bind()
        if(entry==null) {
            if(dao.runningInbox()!=null || dao.nextQueuedInbox()!=null)return
            val items=platform.request("GET","/api/v1/terminal/quick-tests?limit=20",identity.credential).getJSONArray("items")
            if(items.length()==0)return
            val item=items.getJSONObject(0)
            if(item.getString("target_device_id")!=identity.targetDeviceId)return
            val id=item.getString("session_id")
            val claim=platform.request("POST","/api/v1/terminal/quick-tests/$id/claim",identity.credential,JSONObject())
            entry=QuickTestReceiver(context,database).receive(claim,identity,origin)
        }
        if(entry.state=="result_pending") {reportResult(entry,client,platform,identity);return}
        if(entry.state=="queued" && System.currentTimeMillis()>=entry.expiresAt) {
            runCatching {client.cancel(entry.sessionId)}
            val control=platform.request("GET","/api/v1/terminal/quick-tests/${entry.sessionId}/control",identity.credential)
            if(control.getString("status") in setOf("expired","cancelled","completed"))
                dao.updateQuickTest(entry.sessionId,"completed",JSONObject().put("passed",false)
                    .put("error_code","quick_test_expired_unstarted").toString(),System.currentTimeMillis())
            return
        }
        if(entry.state=="queued") {
            if(!BrokerClient.codeCurrent(context))return
            if(dao.runningInbox()!=null || dao.nextQueuedInbox()!=null)return
            val body=CanonicalBodyStore(File(context.filesDir,"maa/bodies")).read(entry.packageHash,"")
            client.prepareBody(entry.sessionId,entry.packageHash,body,entry.sessionId,entry.expiresAt,
                dao.quickResources(entry.sessionId).map {PackageResourceSpec(it.resourceKey,it.blobId,it.sha256,it.size,it.mediaType,it.role)})
            if(dao.markQuickRunning(entry.sessionId,System.currentTimeMillis())!=1)return
        }
        val control=platform.request("GET","/api/v1/terminal/quick-tests/${entry.sessionId}/control",identity.credential)
        var status=client.status(entry.sessionId)
        require(status.getString("package_hash")==entry.packageHash)
        if(!proof(status))return
        if(control.optBoolean("cancel_requested") || control.getString("status") in setOf("cancelled","expired") ||
            dao.nextQueuedInbox()!=null || dao.runningInbox()!=null || System.currentTimeMillis()>=entry.expiresAt) {
            if(status.getString("status") in setOf("accepted","running"))status=client.cancel(entry.sessionId)
        } else if(status.getString("status")=="accepted") {
            if(!BrokerClient.codeCurrent(context))return
            status=client.start(entry.sessionId)
        }
        importEvents(entry,client,status)
        uploadEvents(entry,platform,identity)
        if(status.getString("status") in setOf("completed","interrupted")) {
            val result=JSONObject().put("passed",status.getString("result")=="success")
                .put("error_code",status.getString("error_code") ?: JSONObject.NULL)
                .put("diagnostic",status.getString("diagnostic")?.let {JSONObject(it)} ?: JSONObject())
            dao.updateQuickTest(entry.sessionId,"result_pending",result.toString(),System.currentTimeMillis())
        }
    }
    private suspend fun importEvents(entry:QuickTestInboxEntity,client:BrokerExecutionClient,status:android.os.Bundle) {
        val events=JSONArray(client.events(entry.sessionId,entry.lastHelperSequence).getString("events").orEmpty())
        var helper=entry.lastHelperSequence;var sequence=entry.lastEventSequence
        val projected=mutableListOf<QuickTestEventEntity>()
        if(sequence==0 && status.getString("status")!="accepted") {
            projected+=QuickTestEventEntity(entry.sessionId,++sequence,"started",null,null,Instant.now().toString())
        }
        for(i in 0 until events.length()) {
            val event=events.getJSONObject(i);helper=maxOf(helper,event.getLong("sequence"))
            val value=QuickEventProjection.project(event.getString("message"),event,entry.entryDefinitionKey,entry.debugStepNumber) ?: continue
            if(sequence>=1000)continue
            projected+=QuickTestEventEntity(entry.sessionId,++sequence,value.kind,value.step,value.code?.take(100),
                event.getString("occurred_at"))
        }
        database.withTransaction {
            database.terminalDao().insertQuickEvents(projected)
            database.terminalDao().updateQuickCursor(entry.sessionId,helper,sequence)
        }
    }
    private fun uploadEvents(entry:QuickTestInboxEntity,platform:PlatformClient,identity:TerminalIdentity) {
        val pending=database.terminalDao().pendingQuickEvents(entry.sessionId)
        if(pending.isEmpty())return
        val response=platform.request("POST","/api/v1/terminal/quick-tests/${entry.sessionId}/events",identity.credential,
            JSONObject().put("items",JSONArray(pending.map {JSONObject().put("sequence",it.sequence).put("kind",it.kind)
                .put("step_number",it.stepNumber ?: JSONObject.NULL).put("code",it.code ?: JSONObject.NULL).put("created_at",it.createdAt)})))
        val confirmed=response.getInt("last_sequence")
        check(confirmed>=pending.last().sequence) {"quick_events_not_confirmed"}
        database.terminalDao().confirmQuickEvents(entry.sessionId,confirmed)
    }
    private suspend fun reportResult(entry:QuickTestInboxEntity,client:BrokerExecutionClient,platform:PlatformClient,identity:TerminalIdentity) {
        val control=platform.request("GET","/api/v1/terminal/quick-tests/${entry.sessionId}/control",identity.credential)
        if(control.getString("status") in setOf("expired","cancelled")) {
            // Expiry is not a result/media receipt. Retain the pending report and all evidence.
            return
        }
        val result=JSONObject(checkNotNull(entry.resultJson))
        val localFailure=QuickPreflightResult.isLocalFailure(result)
        if(!localFailure) {
            val status=client.status(entry.sessionId)
            if(status.getString("package_hash")!=entry.packageHash || !proof(status) ||
                status.getString("status") !in setOf("completed","interrupted"))return
            importEvents(entry,client,status)
            val current=checkNotNull(database.terminalDao().activeQuickTest(identity.terminalId))
            if(current.lastHelperSequence<status.getLong("last_event_sequence"))return
            uploadEvents(entry,platform,identity)
            if(database.terminalDao().pendingQuickEvents(entry.sessionId).isNotEmpty())return
        }
        val mapping=if(localFailure)emptyMap() else MaaArtifactDispatcher(PlatformArtifactClient(platform),client)
            .flush(identity.credential,entry.sessionId,"quick_test")
        val nested=ArtifactReferences.resolve(result.getJSONObject("diagnostic"),mapping)
        val detail=nested.optJSONArray("modules")?.optJSONObject(0)?.optJSONObject("result") ?: nested
        QuickDiagnosticProjection.restoreStep(detail,entry.debugStepNumber)
        val body=JSONObject().put("session_id",entry.sessionId).put("candidate_version_id",entry.candidateVersionId)
            .put("candidate_manifest_hash",entry.candidateManifestHash).put("definition_hash",entry.definitionHash)
            .put("executor_version",PlatformClient.AGENT_VERSION).put("passed",result.getBoolean("passed"))
            .put("error_code",result.opt("error_code") ?: JSONObject.NULL).put("diagnostic",detail)
        val response=platform.request("POST","/api/v1/maa/scripts/${entry.scriptId}/quick-test-results",identity.credential,
            body,mapOf("Idempotency-Key" to entry.reportId))
        check(response.getString("session_id")==entry.sessionId && response.getString("session_status")=="completed")
        database.terminalDao().updateQuickTest(entry.sessionId,"completed",entry.resultJson,System.currentTimeMillis())
    }
    private fun proof(status:android.os.Bundle)=HelperAttemptProof.valid(status.getString("status"),
        status.getString("owner_epoch"),status.getString("runtime_epoch"),null,status.getBoolean("started"))
}
