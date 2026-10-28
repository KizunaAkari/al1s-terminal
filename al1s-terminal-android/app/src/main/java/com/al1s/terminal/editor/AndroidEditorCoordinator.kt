package com.al1s.terminal.editor

import com.al1s.terminal.broker.BrokerClient
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.security.IdentityStore
import com.al1s.terminal.security.TerminalConnectionGate
import okhttp3.OkHttpClient
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

@androidx.annotation.RequiresApi(27)
class AndroidEditorCoordinator(private val context:android.content.Context,private val identities: IdentityStore) : AutoCloseable {
    private val instance = UUID.randomUUID().toString()
    private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(8, TimeUnit.SECONDS).writeTimeout(3, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS).pingInterval(20, TimeUnit.SECONDS).build()
    private var active: AndroidEditorSession? = null
    private var identityKey: Pair<String, String>? = null

    private data class CycleInput(val identity:com.al1s.terminal.security.TerminalIdentity,
        val origin:String,val platform:PlatformClient,val sessions:org.json.JSONArray)

    fun cycle(refreshPaths:()->Unit = {}) {
        val input=TerminalConnectionGate.withConnection {
            val identity=identities.load() ?: return@withConnection null
            val origin=identities.configuration()?.first ?: return@withConnection null
            val platform=PlatformClient(origin)
            CycleInput(identity,origin,platform,platform.editorSessions(identity.credential))
        } ?: run {close();return}
        // Paths acquire their own monitor before the connection gate and may release
        // this editor. Hold neither lock here, including across a pending identity commit.
        if(EditorPreparation.needsPathRefresh(input.sessions,input.identity.targetDeviceId))refreshPaths()
        TerminalConnectionGate.withConnection {
            if(identities.load()?.credential!=input.identity.credential ||
                identities.configuration()?.first!=input.origin)return@withConnection close()
            synchronized(this) {reconcile(input.identity,input.origin,input.platform,input.sessions,refreshPaths)}
        }
    }

    private fun reconcile(identity:com.al1s.terminal.security.TerminalIdentity,origin:String,
                          platform:PlatformClient,sessions:org.json.JSONArray,refreshPaths:()->Unit) {
        val key = origin to identity.credential
        if (identityKey != key) { close(); identityKey = key }
        if (!BrokerClient.connected()) return close()
        if(!BrokerClient.codeCurrent(context)) {releaseAndReport();return}
        for (index in 0 until sessions.length()) {
            val session = sessions.getJSONObject(index)
            if (session.getString("device_id") != identity.targetDeviceId) continue
            val id = session.getString("session_id")
            val status = session.getString("status")
            if (status == "closing") {
                if (active?.id == id) { active?.close(); active = null }
                report(platform, identity.credential, id, "closed")
            } else if(status=="active" && active?.id!=id) {
                report(platform,identity.credential,id,"closed")
            } else if (status == "pending" && active == null) {
                val attempt=runCatching {BrokerClient.invoke("editor_prepare")}
                if(attempt.isFailure) {
                    report(platform,identity.credential,id,"failed")
                    continue
                }
                val preparation=attempt.getOrThrow()
                if(!preparation.getBoolean("prepared") && !preparation.getBoolean("automation"))continue
                val connection = JSONObject().put("transport", "android-reverse-v1").put("scrcpy_version", "3.3.4")
                EditorEndpoint.channels.forEach { channel -> connection.put("${channel}_ws_url",
                    "/api/v1/editor-sessions/$id/channels/$channel") }
                val reply=report(platform, identity.credential, id, "active", connection)
                if(EditorPreparation.activationAccepted(reply))
                    active = AndroidEditorSession(origin, identity.credential, id, instance, client) {
                        // Invoked by the media worker before taking the session monitor.
                        refreshPaths()
                        TerminalConnectionGate.withConnection {
                            check(identities.load()?.credential==identity.credential &&
                                identities.configuration()?.first==origin) {"editor_identity_changed"}
                            BrokerClient.invoke("editor_prepare")
                        }
                    }
            }
        }
        active?.connectMissing()
    }

    private fun report(platform: PlatformClient, credential: String, id: String, status: String,
        connection: JSONObject? = null) = platform.editorReport(credential, id,
        JSONObject().put("instance_id", instance).put("status", status).apply {
            if (connection != null) put("connection", connection)
        })

    @Synchronized override fun close() { active?.close(); active = null }
    fun releaseAndReport() = TerminalConnectionGate.withConnection {
        synchronized(this) {
            val session=active ?: return@synchronized
            close()
            val identity=identities.load() ?: return@synchronized
            val origin=identities.configuration()?.first ?: return@synchronized
            report(PlatformClient(origin),identity.credential,session.id,"closed")
        }
    }
}
