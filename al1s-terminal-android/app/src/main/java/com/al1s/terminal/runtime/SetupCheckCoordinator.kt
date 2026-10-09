package com.al1s.terminal.runtime

import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.os.PowerManager
import com.al1s.terminal.activation.WirelessRecovery
import com.al1s.terminal.broker.BrokerClient
import com.al1s.terminal.data.SetupCheckReportEntity
import com.al1s.terminal.data.TerminalDatabase
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.security.IdentityStore
import com.al1s.terminal.security.TerminalConnectionGate
import org.json.JSONObject
import java.util.UUID

/** The server decides when 24 hours are due; heartbeats never refresh completion. */
class SetupCheckCoordinator(private val context:Context,private val database:TerminalDatabase,
    private val identities:IdentityStore) {
    private val instance=UUID.randomUUID().toString()
    @Synchronized fun cycle() = TerminalConnectionGate.withConnection {
        val identity=identities.load() ?: return@withConnection
        val configuration=identities.configuration() ?: return@withConnection
        val platform=PlatformClient(configuration.first)
        val dao=database.terminalDao()
        val pending=dao.pendingSetupReport(identity.terminalId)
        if(pending!=null) {
            val response=platform.request("POST","/api/v1/terminal/setup-checks/${pending.requestId}/complete",
                identity.credential,JSONObject(pending.payloadJson))
            check(response.getString("status")=="completed")
            dao.confirmSetupReport(pending.requestId,identity.terminalId)
            return@withConnection
        }
        if(BrokerClient.connected() && BrokerClient.invoke("path_state").getBoolean("busy"))return@withConnection
        val request=platform.request("GET","/api/v1/terminal/setup-checks/pending",identity.credential)
            .optJSONObject("check") ?: return@withConnection
        val id=request.getString("request_id")
        val claim=platform.request("POST","/api/v1/terminal/setup-checks/$id/claim",identity.credential,
            JSONObject().put("instance_id",instance))
        if(claim.getString("status")=="completed")return@withConnection
        val recovery=WirelessRecovery(context)
        val facts=JSONObject().put("secure_lock",context.getSystemService(KeyguardManager::class.java).isDeviceSecure)
            .put("provider_ready",BrokerClient.connected())
            .put("battery_exempt",context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName))
            .put("notifications_allowed",context.getSystemService(NotificationManager::class.java).areNotificationsEnabled())
            .put("wireless_recovery_enabled",recovery.consented() && recovery.granted())
            .put("manufacturer_policy","unverifiable")
        dao.insertSetupReport(SetupCheckReportEntity(id,identity.terminalId,instance,
            JSONObject().put("instance_id",instance).put("facts",facts).toString(),createdAt=System.currentTimeMillis()))
    }
}
