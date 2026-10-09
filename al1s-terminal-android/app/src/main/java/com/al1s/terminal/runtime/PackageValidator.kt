package com.al1s.terminal.runtime

import com.al1s.terminal.protocol.CanonicalJson
import com.al1s.terminal.protocol.TaskPackage
import com.al1s.terminal.security.TerminalIdentity
import java.security.MessageDigest

data class PackageResourceSpec(val key: String, val blobId: String, val sha256: String, val size: Long, val mediaType: String, val role: String)
data class ValidatedPackage(val action: String, val canonicalBody: String = "", val resources: List<PackageResourceSpec> = emptyList())

class PackageValidationException(
    val code: String,
    message: String,
    cause: Throwable? = null,
) : RuntimeException(message, cause)

object PackageValidator {
    fun validate(taskPackage: TaskPackage, identity: TerminalIdentity): ValidatedPackage = try {
        validatePackage(taskPackage, identity)
    } catch (error: PackageValidationException) {
        throw error
    } catch (error: RuntimeException) {
        throw PackageValidationException(
            "package_schema_invalid",
            "Task package schema is invalid",
            error,
        )
    }

    private fun validatePackage(
        taskPackage: TaskPackage,
        identity: TerminalIdentity,
    ): ValidatedPackage {
        val body = taskPackage.body
        requireMatch(taskPackage.packageId, body.optString("package_id"), "package_id_mismatch")
        requireMatch(taskPackage.attemptId, body.optString("attempt_id"), "attempt_id_mismatch")
        requireMatch(identity.terminalId, body.optString("terminal_id"), "terminal_id_mismatch")
        requireMatch(
            identity.targetDeviceId,
            body.optString("target_device_id"),
            "target_device_id_mismatch",
        )
        val canonical = try { com.al1s.terminal.protocol.PackageCanonicalText.verified(taskPackage) }
            catch (error: IllegalArgumentException) { throw PackageValidationException("package_hash_mismatch", "Task package hash differs", error) }
        if (body.optInt("protocol_version") != 1 || body.optInt("package_schema_version") != 1) {
            throw PackageValidationException("protocol_version_unsupported", "Protocol is unsupported")
        }
        val source = body.optJSONObject("source")
            ?: throw PackageValidationException("package_source_missing", "Package source is missing")
        if (source.optString("module") == "maa") return maa(body, canonical)
        if (source.optString("module") != "android_demo") {
            throw PackageValidationException("source_module_unsupported", "Source module is unsupported")
        }
        val resources = body.optJSONArray("resources")
            ?: throw PackageValidationException(
                "package_schema_invalid",
                "Package resources must be an array",
            )
        if (resources.length() != 0) {
            throw PackageValidationException("package_resources_unsupported", "Demo accepts no resources")
        }
        val manifest = body.optJSONObject("manifest")
            ?: throw PackageValidationException("demo_manifest_missing", "Demo manifest is missing")
        if (manifest.optString("executor") != "xiaomi_root_demo") {
            throw PackageValidationException("demo_executor_unsupported", "Executor is unsupported")
        }
        val action = manifest.optString("action")
        if (!RootDemoExecutor.supports(action)) {
            throw PackageValidationException("android_demo_action_unsupported", "Action is unsupported")
        }
        return ValidatedPackage(action, canonical)
    }

    private fun maa(body: org.json.JSONObject, canonical: String): ValidatedPackage {
        val resources = body.getJSONArray("resources")
        require(resources.length() <= 1000 && body.getInt("timeout_seconds") in 1..86400)
        val manifest = body.getJSONObject("manifest")
        require(manifest.optInt("schema_version") == 1 && manifest.optString("definition_type") in setOf("script", "strategy"))
        val keys = mutableSetOf<String>()
        val specs = (0 until resources.length()).map { index ->
            val value = resources.getJSONObject(index)
            val key = value.getString("resource_key")
            require(key.length in 1..255 && keys.add(key))
            val blob = java.util.UUID.fromString(value.getString("blob_id")).toString()
            val hash = value.getString("sha256"); val size = value.getLong("size")
            require(hash.matches(Regex("[0-9a-f]{64}")) && size in 1..(32L*1024*1024))
            PackageResourceSpec(key, blob, hash, size, value.getString("media_type"), value.getString("role"))
        }
        require(specs.distinctBy { it.sha256 }.sumOf { it.size } <= 256L*1024*1024)
        return ValidatedPackage("maa", canonical, specs)
    }

    private fun requireMatch(expected: String, actual: String, code: String) {
        if (expected != actual) throw PackageValidationException(code, "Task package identity differs")
    }

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte) }
}
