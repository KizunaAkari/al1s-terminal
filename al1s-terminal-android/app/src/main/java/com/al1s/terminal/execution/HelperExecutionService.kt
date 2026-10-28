package com.al1s.terminal.execution

import android.content.Context
import android.os.Bundle
import android.os.ParcelFileDescriptor
import com.al1s.terminal.maa.OcrResources
import com.al1s.terminal.resources.VerifiedResourceStore
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

class HelperExecutionService(private val context: Context, libraries: String, private val workspace: HelperWorkspace,
    private val epoch: String, private val revokeManualInput: () -> Unit,
    private val debugAllowed:()->Boolean={false}) : AutoCloseable {
    private val database = HelperJournal.open(context, File(workspace.directory, "journal.db"))
    private val repository = HelperJournalRepository(database, epoch,
        com.al1s.terminal.resources.CanonicalBodyStore(File(workspace.directory,"bodies")))
    private val resources = VerifiedResourceStore(File(workspace.directory, "resources"))
    private val ocr = OcrResources(context.packageManager.getApplicationInfo("com.al1s.terminal", 0).sourceDir)
    private val runner = NativeMaaRunner(context, libraries, workspace.directory, ocr)
    private val executor = Executors.newSingleThreadExecutor()
    private var active: String? = null
    private var cancelled = AtomicBoolean(false)
    private var sequence = AtomicLong(0)
    private var binding: Pair<String, String>? = null

    init { executor.submit { repository.recoverDeadOwner() }.get() }

    @Synchronized fun canReplace() = active == null && repository.running() == null
    @Synchronized fun busy() = active != null
    @Synchronized fun invoke(operation: String, payload: Bundle): Bundle = when (operation) {
        "bind_identity" -> {
            val terminal = java.util.UUID.fromString(payload.getString("terminal_id")).toString()
            val device = java.util.UUID.fromString(payload.getString("device_id")).toString()
            check(active == null || binding == terminal to device) { "binding_busy" }
            binding = terminal to device
            Bundle()
        }
        "resource_import" -> importResource(payload)
        "task_prepare" -> prepare(payload)
        "task_start" -> start(payload.getString("attempt_id").orEmpty())
        "task_status" -> {
            val id=java.util.UUID.fromString(payload.getString("attempt_id")).toString()
            check(binding!=null) {"terminal_binding_required"}
            if(repository.lookup(id)==null)Bundle().apply {putString("status","absent");putString("runtime_epoch",epoch)}
            else status(id)
        }
        "task_cancel" -> {
            val id = payload.getString("attempt_id").orEmpty()
            requireOwner(id)
            repository.cancel(id)
            if (active == id) cancelled.set(true)
            status(id)
        }
        "task_settle_unstarted" -> {
            val id=payload.getString("attempt_id").orEmpty();requireOwner(id)
            check(repository.settleExpiredUnstarted(id)==1) {"attempt_not_expired_unstarted"}
            status(id)
        }
        "task_events" -> {
            val id = payload.getString("attempt_id").orEmpty()
            requireOwner(id)
            val values = repository.events(id, payload.getLong("after", 0))
            Bundle().apply { putString("events", org.json.JSONArray(values.map {
                JSONObject(it.payloadJson).put("event_id", it.eventId).put("sequence", it.sequence).put("message", it.message)
                    .put("occurred_at",java.time.Instant.ofEpochMilli(it.occurredAt).toString())
            }).toString()) }
        }
        "task_artifacts" -> {
            val id=payload.getString("attempt_id").orEmpty();requireOwner(id)
            Bundle().apply {putString("artifacts",org.json.JSONArray(repository.artifacts(id).map {
                JSONObject().put("artifact_id",it.artifactId).put("kind",it.kind).put("sha256",it.sha256).put("size",it.size).put("media_type",it.mediaType)
                    .put("remote_artifact_id",it.remoteArtifactId ?: JSONObject.NULL)
            }).toString())}
        }
        "artifact_open" -> {
            val value=checkNotNull(repository.artifact(payload.getString("artifact_id").orEmpty()))
            requireOwner(value.attemptId)
            val file=File(value.path);require(file.isFile && !java.nio.file.Files.isSymbolicLink(file.toPath()))
            val parent=file.canonicalFile.parentFile
            require(file.canonicalPath.startsWith(workspace.directory.canonicalPath+"/") ||
                parent?.canonicalPath?.startsWith("/data/local/tmp/al1s-evidence-")==true)
            Bundle().apply {putParcelable("artifact",ParcelFileDescriptor.open(file,ParcelFileDescriptor.MODE_READ_ONLY));putLong("size",value.size)}
        }
        "artifact_confirm" -> {
            val value=checkNotNull(repository.artifact(payload.getString("artifact_id").orEmpty()));requireOwner(value.attemptId)
            val remote=java.util.UUID.fromString(payload.getString("remote_artifact_id")).toString()
            check(repository.confirmArtifact(value.artifactId,remote)==1);Bundle()
        }
        else -> error("unsupported_execution_operation")
    }

    private fun importResource(payload: Bundle): Bundle {
        check(binding != null) { "terminal_binding_required" }
        val hash = payload.getString("sha256").orEmpty(); val size = payload.getLong("size")
        @Suppress("DEPRECATION") val descriptor = checkNotNull(payload.getParcelable<ParcelFileDescriptor>("file"))
        ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { input ->
            resources.receive(hash, size) { _, count ->
                val block = ByteArray(count); var offset = 0
                while (offset < count) { val read = input.read(block, offset, count - offset); check(read > 0); offset += read }
                block
            }
        }
        return Bundle().apply { putBoolean("ready", true) }
    }

    private fun prepare(payload: Bundle): Bundle {
        val owner = checkNotNull(binding) { "terminal_binding_required" }
        val body = readBody(payload)
        val parsed = JSONObject(body)
        require(parsed.getJSONObject("source").getString("module") == "maa") { "source_module_unsupported" }
        val request = AuthorizedAttempt(parsed.getString("attempt_id"), parsed.getString("package_id"),
            payload.getString("package_hash").orEmpty(), parsed.getString("terminal_id"), parsed.getString("target_device_id"),
            payload.getString("permit_id").orEmpty(), payload.getLong("permit_expires_at"), parsed.getInt("timeout_seconds"),parsed.optString("owner_kind","formal"))
        request.validate(owner.first, owner.second, System.currentTimeMillis())
        val hash = MessageDigest.getInstance("SHA-256").digest(body.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        require(hash == request.packageHash) { "package_hash_mismatch" }
        runner.prepare(parsed, resourcePaths(parsed))
        repository.accept(request, body)
        return status(request.attemptId)
    }

    private fun start(id: String): Bundle {
        val task = requireOwner(id)
        if (task.status != "accepted") return status(id)
        check(active == null) { "native_execution_busy" }
        check(task.permitExpiresAt > System.currentTimeMillis()) { "offline_permit_expired" }
        if(task.ownerKind=="quick_test")check(debugAllowed()) {"debug_input_authority_unavailable"}
        val body = JSONObject(repository.body(task))
        val prepared = runner.prepare(body, resourcePaths(body))
        revokeManualInput()
        check(repository.start(id)) { "attempt_already_started" }
        active = id; cancelled = AtomicBoolean(task.cancellationRequested); sequence = AtomicLong(0)
        val cancellation = cancelled; val eventSequence = sequence
        executor.execute {
            val result = try {
                runner.run(prepared, {cancellation.get() || task.ownerKind=="quick_test" &&
                    (!debugAllowed() || System.currentTimeMillis()>=task.permitExpiresAt)}) { module, message, event ->
                    repository.captureArtifact(id,message,event)
                    repository.append(id, eventSequence.incrementAndGet(), message, event, module, event.opt("step_index") as? Int)
                }
            } catch (error: Throwable) {
                NativeExecutionResult("failure", "native_execution_failed", JSONObject().put("cause", error.javaClass.simpleName))
            }
            repository.complete(id, result.result, result.errorCode, result.diagnostic)
            synchronized(this) { active = null }
        }
        return status(id)
    }

    private fun requireOwner(id: String): HelperAttemptEntity {
        java.util.UUID.fromString(id)
        val owner = checkNotNull(binding) { "terminal_binding_required" }
        return checkNotNull(repository.lookup(id)).also { require(it.terminalId == owner.first && it.deviceId == owner.second) }
    }
    private fun status(id: String): Bundle {
        val value = requireOwner(id)
        return Bundle().apply {
            putString("status", value.status); putString("package_hash", value.packageHash); putString("owner_epoch", value.ownerEpoch)
            putString("runtime_epoch",epoch);putBoolean("started",value.startedAt!=null)
            putLong("last_event_sequence",database.dao().latestSequence(id) ?: 0)
            putString("result", value.result); putString("error_code", value.errorCode); putString("diagnostic", value.diagnosticJson)
        }
    }
    private fun resourcePaths(body: JSONObject): Map<String, File> {
        val array = body.getJSONArray("resources")
        require(array.length() <= 1000)
        return (0 until array.length()).associate { index ->
            val value = array.getJSONObject(index)
            value.getString("resource_key") to checkNotNull(resources.ready(value.getString("sha256"), value.getLong("size"))) {
                "resource_not_ready"
            }
        }
    }
    private fun readBody(payload: Bundle): String {
        val length = payload.getInt("length"); require(length in 1..(4*1024*1024))
        @Suppress("DEPRECATION") val descriptor = checkNotNull(payload.getParcelable<ParcelFileDescriptor>("body"))
        return ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { input ->
            val bytes = ByteArray(length); var offset = 0
            while (offset < length) { val size = input.read(bytes, offset, length-offset); check(size>0); offset += size }
            check(input.read() == -1)
            bytes.toString(Charsets.UTF_8)
        }
    }
    @Synchronized override fun close() { check(active == null); executor.shutdown(); ocr.close(); database.close() }
}
