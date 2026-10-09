package com.al1s.terminal.protocol

import java.net.URL
import javax.net.ssl.HttpsURLConnection

object PlatformTlsProbe {
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
