package com.al1s.terminal.protocol

import org.json.JSONObject
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

class PlatformArtifactClient(private val platform:PlatformClient) {
    fun create(credential:String,attempt:String,idempotency:String,kind:String,hash:String,size:Long,type:String,ownerKind:String="formal_attempt"):JSONObject =
        platform.request("POST","/api/v1/terminal/artifacts/uploads",credential,JSONObject().put("owner_kind",ownerKind)
            .put("owner_id",attempt).put("artifact_kind",if(kind=="recording")"video" else "screenshot")
            .put("file_name",idempotency+if(kind=="recording")".mp4" else ".png").put("sha256",hash)
            .put("size_bytes",size).put("media_type",type),mapOf("Idempotency-Key" to idempotency))
    fun complete(credential:String,id:String):JSONObject = platform.request("POST","/api/v1/terminal/artifacts/uploads/$id/complete",credential,JSONObject())
    fun upload(instructions:JSONObject,size:Long,input:InputStream) {
        require(instructions.getString("method")=="PUT")
        val url=URL(instructions.getString("url"))
        require(url.protocol=="https" && url.userInfo==null) {"artifact_upload_requires_https"}
        val connection=url.openConnection() as HttpURLConnection
        try {
            connection.instanceFollowRedirects=false;connection.requestMethod="PUT";connection.doOutput=true
            connection.connectTimeout=15000;connection.readTimeout=30000;connection.setFixedLengthStreamingMode(size)
            val headers=instructions.getJSONObject("headers")
            headers.keys().forEach { key->require(!key.equals("Authorization",true));connection.setRequestProperty(key,headers.getString(key)) }
            connection.outputStream.use {output->
                val buffer=ByteArray(65536);var sent=0L
                while(sent<size) {val count=input.read(buffer,0,minOf(buffer.size.toLong(),size-sent).toInt());check(count>0);output.write(buffer,0,count);sent+=count}
                check(input.read()==-1)
            }
            val status=connection.responseCode
            if(status !in 200..299)throw PlatformException(status,"artifact_upload_failed","Artifact upload failed")
        } finally {connection.disconnect()}
    }
}
