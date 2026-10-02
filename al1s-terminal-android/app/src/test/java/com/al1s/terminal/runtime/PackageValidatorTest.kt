package com.al1s.terminal.runtime

import com.al1s.terminal.protocol.CanonicalJson
import com.al1s.terminal.protocol.TaskPackage
import com.al1s.terminal.security.TerminalIdentity
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class PackageValidatorTest {
    private val identity = TerminalIdentity(
        installationId = "4ad94947-e65c-469d-8f06-516e95aac668",
        terminalId = "e52b37e4-09f6-4cd9-b193-6ae379684290",
        targetDeviceId = "7d91a17d-b9c7-4597-94cb-029b974786ae",
        credential = "secret",
        terminalRowVersion = 1,
        capabilityRevision = 1,
    )

    @Test
    fun acceptsCanonicalRootProbePackage() {
        val body = body("root_probe")
        val taskPackage = TaskPackage(
            body.getString("package_id"),
            body.getString("attempt_id"),
            sha256(CanonicalJson.encode(body)),
            body,
        )

        assertEquals("root_probe", PackageValidator.validate(taskPackage, identity).action)
    }

    @Test
    fun rejectsAHashValidButUnregisteredAction() {
        val body = body("remote_shell")
        val taskPackage = TaskPackage(
            body.getString("package_id"),
            body.getString("attempt_id"),
            sha256(CanonicalJson.encode(body)),
            body,
        )

        val error = assertThrows(PackageValidationException::class.java) {
            PackageValidator.validate(taskPackage, identity)
        }
        assertEquals("android_demo_action_unsupported", error.code)
    }

    @Test
    fun rejectsMalformedResourcesWithAStableSchemaCode() {
        val body = body("root_probe").put("resources", "not-an-array")
        val taskPackage = TaskPackage(
            body.getString("package_id"),
            body.getString("attempt_id"),
            sha256(CanonicalJson.encode(body)),
            body,
        )

        val error = assertThrows(PackageValidationException::class.java) {
            PackageValidator.validate(taskPackage, identity)
        }
        assertEquals("package_schema_invalid", error.code)
    }

    private fun body(action: String): JSONObject = JSONObject()
        .put("protocol_version", 1)
        .put("package_schema_version", 1)
        .put("package_id", "2537460e-131c-471b-8e1d-d159d4dd5ac9")
        .put("attempt_id", "a978162f-acf3-4de8-a174-d67db59a6c7b")
        .put("terminal_id", identity.terminalId)
        .put("target_device_id", identity.targetDeviceId)
        .put("source", JSONObject().put("module", "android_demo"))
        .put("resources", JSONArray())
        .put(
            "manifest",
            JSONObject()
                .put("schema_version", 1)
                .put("executor", "xiaomi_root_demo")
                .put("action", action),
        )

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray())
        .joinToString("") { byte -> "%02x".format(byte) }
}
