package com.al1s.terminal.runtime

import android.os.Bundle
import com.al1s.terminal.broker.BrokerClient
import com.al1s.terminal.protocol.PlatformClient
import com.al1s.terminal.security.IdentityStore
import com.al1s.terminal.security.TerminalConnectionGate
import org.json.JSONArray
import org.json.JSONObject

class DevicePathCoordinator(private val context:android.content.Context,private val identities:IdentityStore,private val closeEditor:()->Unit) {
    private var releasedGeneration:Long?=null
    private var releasedPreviousInstance:String?=null
    @Synchronized fun cycle()=TerminalConnectionGate.withConnection {
        val identity=identities.load() ?: return@withConnection
        val origin=identities.configuration()?.first ?: return@withConnection
        if(!BrokerClient.connected())return@withConnection
        BrokerClient.invoke("bind_identity",Bundle().apply {
            putString("terminal_id",identity.terminalId);putString("device_id",identity.targetDeviceId)
        })
        val state=BrokerClient.invoke("path_state")
        val instance=checkNotNull(state.getString("instance_id"))
        val observation=JSONObject().put("device_id",identity.targetDeviceId).put("instance_id",instance)
            .put("control_ready",BrokerClient.codeCurrent(context) && state.getBoolean("control_ready"))
        releasedGeneration?.let {observation.put("release_generation",it)}
        releasedPreviousInstance?.let {observation.put("released_previous_instance_id",it)}
        val reply=PlatformClient(origin).request("POST","/api/v1/terminal/device-paths/observations",
            identity.credential,JSONObject().put("items",JSONArray().put(observation)))
            .getJSONArray("items").getJSONObject(0)
        BrokerClient.invoke("authority_update",Bundle().apply {
            putString("instance_id",instance);putLong("generation",reply.getLong("generation"))
            putBoolean("input_granted",reply.getBoolean("input_granted"))
            putBoolean("holder_confirmed",reply.optString("holder_terminal_id")==identity.terminalId &&
                reply.optString("holder_instance_id")==instance)
            putLong("deadline",System.currentTimeMillis()+25000)
        })
        val replaced=reply.optString("holder_terminal_id")==identity.terminalId &&
            reply.optString("holder_instance_id")==state.getString("previous_instance_id") &&
            reply.optString("holder_instance_id")!=instance
        if((reply.getBoolean("release_requested") || replaced) && !state.getBoolean("busy")) {
            closeEditor()
            check(BrokerClient.invoke("path_release").getBoolean("released"))
            releasedGeneration=reply.getLong("generation")
            releasedPreviousInstance=if(replaced)state.getString("previous_instance_id") else null
        } else if(reply.getBoolean("input_granted")) {releasedGeneration=null;releasedPreviousInstance=null}
    }
}
