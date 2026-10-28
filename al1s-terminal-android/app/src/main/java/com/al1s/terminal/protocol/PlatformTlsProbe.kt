package com.al1s.terminal.protocol

import java.net.URL
import javax.net.ssl.HttpsURLConnection

object PlatformTlsProbe {
    fun resultText(status:Int):String = if(status==401)
        "HTTPS 证书与主机名已验证 · HTTP 401（探测未携带认证信息）"
        else "HTTPS 证书与主机名已验证 · HTTP $status"

    fun check(value:String):Int {
        val origin=PlatformEndpoint.normalize(value)
        val connection=URL("$origin/api/v1/system/health/live").openConnection() as HttpsURLConnection
        try {
            connection.instanceFollowRedirects=false;connection.connectTimeout=10000;connection.readTimeout=10000
            connection.requestMethod="GET"
            val status=connection.responseCode
            require(status in 200..499 && status !in 300..399) {"platform_https_status_invalid"}
            return status
        } finally {connection.disconnect()}
    }
}
