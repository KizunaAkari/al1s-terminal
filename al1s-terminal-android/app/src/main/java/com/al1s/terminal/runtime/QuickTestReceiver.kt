package com.al1s.terminal.runtime

import android.content.Context
import androidx.room.withTransaction
import com.al1s.terminal.data.*
import com.al1s.terminal.maa.*
import com.al1s.terminal.protocol.*
import com.al1s.terminal.resources.*
import com.al1s.terminal.security.TerminalIdentity
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID

class QuickTestReceiver(private val context:Context,private val database:TerminalDatabase) {
    suspend fun receive(claim:JSONObject,identity:TerminalIdentity,origin:String):QuickTestInboxEntity {
        val session=claim.getJSONObject("session");val definition=claim.getJSONObject("definition")
        require(session.getString("status")=="claimed" && session.getString("target_device_id")==identity.targetDeviceId)
        val id=UUID.fromString(session.getString("session_id")).toString()
        val text=definition.optString("canonical_manifest").takeIf {it.isNotEmpty()} ?: CanonicalJson.encode(definition.getJSONObject("manifest"))
        require(CanonicalJson.encode(JSONObject(text))==CanonicalJson.encode(definition.getJSONObject("manifest")))
        require(PackageCanonicalText.sha256(text)==definition.getString("manifest_hash")) {"quick_test_manifest_hash_mismatch"}
        val transfer=PlatformBlobClient(origin);val references=definition.getJSONArray("blobs")
        require(references.length()<=1000)
        val specs=(0 until references.length()).map {index->
            val item=references.getJSONObject(index);val blob=UUID.fromString(item.getString("blob_id")).toString()
            val header=transfer.head(identity.credential,blob)
            PackageResourceSpec(item.getString("resource_key"),blob,header.sha256,header.size,header.mediaType,item.getString("role"))
        }
        require(specs.map {it.key}.distinct().size==specs.size && specs.distinctBy {it.sha256}.sumOf {it.size}<=256L*1024*1024)
        val files=MaaPackageResources(VerifiedResourceStore(File(context.filesDir,"maa/resources")),transfer).receive(identity.credential,specs)
        val manifest=JSONObject(text)
        val failure=try {
            validate(manifest,files,id)
            null
        } catch(error:IllegalArgumentException) {QuickPreflightResult.failure(error)}
        val expiry=Instant.parse(session.getString("expires_at")).toEpochMilli()
        val now=System.currentTimeMillis();require(expiry>now)
        val body=JSONObject().put("protocol_version",1).put("package_schema_version",1)
            .put("package_id",id).put("attempt_id",id).put("terminal_id",identity.terminalId).put("target_device_id",identity.targetDeviceId)
            .put("owner_kind","quick_test").put("source",JSONObject().put("module","maa"))
            .put("manifest",manifest).put("timeout_seconds",((expiry-now)/1000).coerceIn(1,86400).toInt()).put("record_video",false)
            .put("resources",JSONArray(specs.map {JSONObject().put("resource_key",it.key).put("blob_id",it.blobId)
                .put("sha256",it.sha256).put("size",it.size).put("media_type",it.mediaType).put("role",it.role)}))
        val canonical=CanonicalJson.encode(body);val hash=PackageCanonicalText.sha256(canonical)
        CanonicalBodyStore(File(context.filesDir,"maa/bodies")).write(hash,canonical)
        val row=QuickTestInboxEntity(id,session.getString("script_id"),session.getString("candidate_version_id"),
            session.getString("candidate_manifest_hash"),session.getString("definition_hash"),identity.terminalId,
            identity.targetDeviceId,hash,if(failure==null)"queued" else "result_pending",expiry,UUID.randomUUID().toString(),manifest.getString("entry_definition_key"),
            (manifest.opt("debug_step_number") as? Int),resultJson=failure?.toString(),createdAt=now,updatedAt=now)
        database.withTransaction {
            if(database.terminalDao().insertQuickTest(row)>=0)database.terminalDao().insertQuickResources(specs.map {
                QuickTestResourceEntity(id,it.key,it.blobId,it.sha256,it.size,it.mediaType,it.role)
            })
        }
        return row
    }
    private fun validate(manifest:JSONObject,files:Map<String,File>,id:String) {
        val plan=AndroidDefinitionLoader.load(manifest,files)
        val folder=File(context.filesDir,"maa/quick-preflight/$id/image")
        CourseAssets.install(context.packageManager.getApplicationInfo(context.packageName,0).sourceDir,folder)
        AndroidPlanCompiler.compile(plan,folder).forEach { (module,pipeline)->
            val handlers=NativeHandlers(MaaEventProjection(),pipeline=pipeline.pipeline,
                device=com.al1s.terminal.device.NativeDeviceActions(context,module.target.optString("application_package").takeIf {it.isNotBlank()}))
            HandlerRegistry.requireAll(pipeline.pipeline,handlers.actionNames.toSet(),handlers.recognitionNames.toSet())
        }
    }
}
