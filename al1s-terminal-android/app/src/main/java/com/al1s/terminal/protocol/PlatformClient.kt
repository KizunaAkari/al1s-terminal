package com.al1s.terminal.protocol

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

enum class PrestartSettlement { EXPIRED_FAILURE, ALREADY_CANCELLED }

class PlatformClient(
    baseUrl: String,
    private val connectionFactory: (URL) -> HttpURLConnection = {
        it.openConnection() as HttpURLConnection
    },
) {
    private val origin = PlatformEndpoint.normalize(baseUrl)
    fun register(
        registrationCode: String,
        installationId: String,
        displayName: String,
        agentVersion: String,
    ): RegistrationResult {
        val response = request(
            "POST",
            "/api/v1/terminals/register",
            body = JSONObject()
                .put("registration_code", registrationCode)
                .put("installation_id", installationId)
                .put("terminal_type", "android")
                .put("display_name", displayName)
                .put("agent_version", agentVersion),
        )
        val terminal = response.getJSONObject("terminal")
        val target = response.getJSONObject("target_device")
        return RegistrationResult(
            terminalId = terminal.getString("terminal_id"),
            targetDeviceId = target.getString("device_id"),
            credential = response.getString("credential"),
            rowVersion = terminal.getInt("row_version"),
        )
    }

    fun heartbeat(credential: String, terminalId: String, rowVersion: Int): Int {
        val response = request(
            "POST",
            "/api/v1/terminals/$terminalId/heartbeat",
            credential,
            JSONObject()
                .put("expected_version", rowVersion)
                .put("service_status", "online")
                .put("acceptance_status", "accepting")
                .put("agent_version", AGENT_VERSION),
        )
        return response.getInt("row_version")
    }

    fun publishCapability(
        credential: String,
        terminalId: String,
        revision: Int,
        capability: JSONObject,
    ): Int {
        val body = JSONObject(capability.toString()).put("revision", revision)
        val response = request(
            "POST",
            "/api/v1/terminals/$terminalId/capability-profiles",
            credential,
            body,
        )
        return response.getInt("revision")
    }

    fun listCommands(credential: String): List<TerminalCommand> {
        val items = request("GET", "/api/v1/terminal/commands?limit=50", credential)
            .getJSONArray("items")
        return (0 until items.length()).map { index ->
            val item = items.getJSONObject(index)
            TerminalCommand(
                commandId = item.getString("command_id"),
                kind = item.getString("kind"),
                packageId = item.optString("package_id").ifBlank { null },
                attemptId = item.getString("attempt_id"),
            )
        }
    }

    fun getTaskPackage(credential: String, packageId: String): TaskPackage {
        val response = request(
            "GET",
            "/api/v1/terminal/task-packages/$packageId",
            credential,
        )
        return TaskPackage(
            packageId = response.getString("package_id"),
            attemptId = response.getString("attempt_id"),
            packageHash = response.getString("package_hash"),
            body = response.getJSONObject("body"),
        )
    }

    fun sendPackageReceipt(credential: String, report: JSONObject): OfflinePermit? {
        val packageId = report.getString("package_id")
        val response = request(
            "POST",
            "/api/v1/terminal/task-packages/$packageId/receipt",
            credential,
            report.without("package_id"),
        )
        val permit = response.optJSONObject("offline_start_permit") ?: return null
        return OfflinePermit(
            permitId = permit.getString("permit_id"),
            permitVersion = permit.getInt("permit_version"),
            token = permit.getString("token"),
            expiresAt = permit.getString("expires_at"),
        )
    }

    fun startAttempt(credential: String, report: JSONObject): AttemptLease {
        val attemptId = report.getString("attempt_id")
        val response = request(
            "POST",
            "/api/v1/terminal/attempts/$attemptId/start",
            credential,
            report.without("attempt_id"),
        )
        return AttemptLease(
            leaseId = response.getString("lease_id"),
            leaseVersion = response.getInt("lease_version"),
        )
    }

    fun settleExpiredPrestart(credential: String, report: JSONObject): PrestartSettlement {
        val attemptId = report.getString("attempt_id")
        val response = request(
            "POST",
            "/api/v1/terminal/attempts/$attemptId/prestart-failure",
            credential,
            report.without("attempt_id"),
        )
        if (response.optString("report_id") != report.getString("report_id")) {
            throw PlatformException(0, "prestart_settlement_incomplete", "Settlement was not confirmed")
        }
        val executionStatus = response.optString("execution_status")
        val attemptStatus = response.optString("attempt_status")
        val disposition = response.optString("disposition")
        if (executionStatus == "ended" && attemptStatus == "ended" && disposition == "accepted") {
            return PrestartSettlement.EXPIRED_FAILURE
        }
        if (executionStatus == "cancelled" && attemptStatus == "cancelled" && disposition == "stale") {
            return PrestartSettlement.ALREADY_CANCELLED
        }
        throw PlatformException(0, "prestart_settlement_incomplete", "Settlement was not confirmed")
    }

    fun sendAttemptResult(credential: String, report: JSONObject) {
        val attemptId = report.getString("attempt_id")
        request(
            "POST",
            "/api/v1/terminal/attempts/$attemptId/result",
            credential,
            report.without("attempt_id"),
        )
    }

    fun acknowledgeCancellation(credential: String, report: JSONObject) {
        val commandId = report.getString("command_id")
        request(
            "POST",
            "/api/v1/terminal/commands/$commandId/acknowledgement",
            credential,
            report.without("command_id"),
        )
    }

    private fun request(
        method: String,
        path: String,
        credential: String? = null,
        body: JSONObject? = null,
    ): JSONObject {
        var connection: HttpURLConnection? = null
        try {
            val current = connectionFactory(URL(origin + path))
            connection = current
            current.instanceFollowRedirects = false
            current.requestMethod = method
            current.connectTimeout = 15_000
            current.readTimeout = 15_000
            current.setRequestProperty("Accept", "application/json")
            if (credential != null) current.setRequestProperty("Authorization", "Bearer $credential")
            if (body != null) {
                current.doOutput = true
                current.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                current.outputStream.use { output ->
                    output.write(body.toString().toByteArray(Charsets.UTF_8))
                }
            }
            val status = current.responseCode
            val text = (if (status in 200..299) current.inputStream else current.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status !in 200..299) throw platformError(status, text)
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } catch (error: IOException) {
            throw PlatformException(0, "platform_unavailable", "Platform is unavailable", error)
        } finally {
            connection?.disconnect()
        }
    }

    private fun platformError(status: Int, text: String): PlatformException {
        val json = runCatching { JSONObject(text) }.getOrNull()
        val detail = json?.optJSONObject("detail")
        val code = detail?.optString("code")?.takeIf { it.isNotBlank() }
            ?: json?.optString("code")?.takeIf { it.isNotBlank() }
            ?: "platform_http_error"
        val message = detail?.optString("message")?.takeIf { it.isNotBlank() }
            ?: json?.optString("message")?.takeIf { it.isNotBlank() }
            ?: "Platform returned HTTP $status"
        return PlatformException(status, code, message)
    }

    private fun JSONObject.without(key: String): JSONObject =
        JSONObject(toString()).also { it.remove(key) }

    companion object {
        const val AGENT_VERSION = "0.1.0-xiaomi-root-demo"
        val PROVIDER_KEYS = JSONArray().put("android.xiaomi.root.demo")
    }
}
