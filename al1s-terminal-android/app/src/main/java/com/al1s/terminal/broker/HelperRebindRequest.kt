package com.al1s.terminal.broker

object HelperRebindRequest {
    fun accepts(uid: Int, nonce: String, apk: String): Boolean =
        uid in setOf(0, 2000) && nonce.matches(Regex("[A-Za-z0-9_-]{40,80}=?")) &&
            apk.length <= 1024 && apk.startsWith("/data/app/") && apk.endsWith("/base.apk") &&
            !apk.contains("..") && !apk.contains('\n') && !apk.contains('\u0000')
}
