package com.al1s.terminal.runtime

import com.al1s.terminal.protocol.CanonicalJson
import com.al1s.terminal.protocol.TaskPackage
import com.al1s.terminal.security.TerminalIdentity
import java.security.MessageDigest

data class ValidatedPackage(val action: String)

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
        val actualHash = sha256(CanonicalJson.encode(body))
        if (actualHash != taskPackage.packageHash) {
            throw PackageValidationException("package_hash_mismatch", "Task package hash differs")
        }
        if (body.optInt("protocol_version") != 1 || body.optInt("package_schema_version") != 1) {
            throw PackageValidationException("protocol_version_unsupported", "Protocol is unsupported")
        }
        val source = body.optJSONObject("source")
            ?: throw PackageValidationException("package_source_missing", "Package source is missing")
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
        return ValidatedPackage(action)
    }

    private fun requireMatch(expected: String, actual: String, code: String) {
        if (expected != actual) throw PackageValidationException(code, "Task package identity differs")
    }

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte) }
}
