package com.al1s.terminal.runtime

import android.content.Context
import android.os.Bundle
import android.os.ParcelFileDescriptor
import com.al1s.terminal.broker.BrokerClient
import com.al1s.terminal.data.InboxTaskEntity
import com.al1s.terminal.data.PackageResourceEntity
import com.al1s.terminal.resources.VerifiedResourceStore
import com.al1s.terminal.security.TerminalIdentity
import java.io.File
import java.time.Instant

class BrokerExecutionClient(private val context: Context, private val identity: TerminalIdentity) {
    private val resources = VerifiedResourceStore(File(context.filesDir, "maa/resources"))
    fun bind() = BrokerClient.invoke("bind_identity", Bundle().apply {
        putString("terminal_id", identity.terminalId); putString("device_id", identity.targetDeviceId)
    })
    fun prepare(task: InboxTaskEntity, references: List<PackageResourceEntity>): Bundle {
        val body = com.al1s.terminal.resources.CanonicalBodyStore(File(context.filesDir,"maa/bodies"))
            .read(task.packageHash,task.bodyJson)
        return prepareBody(task.packageId,task.packageHash,body,checkNotNull(task.permitId),
            Instant.parse(task.permitExpiresAt).toEpochMilli(),references.map {PackageResourceSpec(
                it.resourceKey,it.blobId,it.sha256,it.size,it.mediaType,it.role)})
    }
    fun prepareBody(packageId:String,hash:String,body:String,permit:String,expiry:Long,
        references:List<PackageResourceSpec>):Bundle {
        check(BrokerClient.connected()) { "control_activation_required" }
        BrokerClient.invoke("bind_identity", Bundle().apply { putString("terminal_id", identity.terminalId); putString("device_id", identity.targetDeviceId) })
        references.distinctBy { it.sha256 }.forEach { resource ->
            val file = checkNotNull(resources.ready(resource.sha256, resource.size)) { "resource_not_ready" }
            ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
                BrokerClient.invoke("resource_import", Bundle().apply { putString("sha256", resource.sha256); putLong("size", resource.size); putParcelable("file", descriptor) })
            }
        }
        val root = File(context.filesDir, "maa/packages").apply { check(isDirectory || mkdirs()) }
        val id = java.util.UUID.fromString(packageId).toString()
        val file = File(root, "$id.json")
        java.io.FileOutputStream(file).use { it.write(body.toByteArray(Charsets.UTF_8)); it.fd.sync() }
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
            BrokerClient.invoke("task_prepare", Bundle().apply {
                putParcelable("body", descriptor); putInt("length", file.length().toInt()); putString("package_hash", hash)
                putString("permit_id", permit); putLong("permit_expires_at", expiry)
            })
        }
    }
    fun start(attempt: String) = BrokerClient.invoke("task_start", id(attempt))
    fun status(attempt: String) = BrokerClient.invoke("task_status", id(attempt))
    fun cancel(attempt: String) = BrokerClient.invoke("task_cancel", id(attempt))
    fun settleUnstarted(attempt:String)=BrokerClient.invoke("task_settle_unstarted",id(attempt))
    fun events(attempt: String, after: Long) = BrokerClient.invoke("task_events", id(attempt).apply { putLong("after", after) })
    fun artifacts(attempt:String)=BrokerClient.invoke("task_artifacts",id(attempt))
    fun openArtifact(artifact:String)=BrokerClient.invoke("artifact_open",Bundle().apply {putString("artifact_id",artifact)})
    fun confirmArtifact(artifact:String,remote:String)=BrokerClient.invoke("artifact_confirm",Bundle().apply {
        putString("artifact_id",artifact);putString("remote_artifact_id",remote)
    })
    private fun id(attempt: String) = Bundle().apply { putString("attempt_id", java.util.UUID.fromString(attempt).toString()) }
}
