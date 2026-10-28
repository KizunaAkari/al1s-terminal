package com.al1s.terminal.runtime

import android.os.ParcelFileDescriptor
import com.al1s.terminal.protocol.PlatformArtifactClient
import org.json.JSONObject

class MaaArtifactDispatcher(private val platform:PlatformArtifactClient,private val helper:BrokerExecutionClient) {
    fun flush(credential:String,attempt:String,ownerKind:String="formal_attempt"):Map<String,String> {
        val values=org.json.JSONArray(helper.artifacts(attempt).getString("artifacts").orEmpty())
        val confirmed=mutableMapOf<String,String>()
        for(index in 0 until values.length()) {
            val artifact=values.getJSONObject(index);val local=artifact.getString("artifact_id")
            val existing=artifact.optString("remote_artifact_id").takeIf {it.isNotEmpty() && it!="null"}
            if(existing!=null) {confirmed[local]=existing;continue}
            val response=platform.create(credential,attempt,local,artifact.getString("kind"),artifact.getString("sha256"),
                artifact.getLong("size"),artifact.getString("media_type"),ownerKind)
            var result=response
            if(response.getString("status")!="ready") {
                val instructions=response.optJSONObject("upload") ?: error("artifact_upload_pending")
                val source=helper.openArtifact(local)
                @Suppress("DEPRECATION") val descriptor=checkNotNull(source.getParcelable<ParcelFileDescriptor>("artifact"))
                ParcelFileDescriptor.AutoCloseInputStream(descriptor).use {platform.upload(instructions,artifact.getLong("size"),it)}
                result=platform.complete(credential,response.getString("artifact_id"))
            }
            check(result.getString("status")=="ready") {"artifact_verification_pending"}
            confirmed[local]=result.getString("artifact_id")
            helper.confirmArtifact(local,result.getString("artifact_id"))
        }
        return confirmed
    }
}
