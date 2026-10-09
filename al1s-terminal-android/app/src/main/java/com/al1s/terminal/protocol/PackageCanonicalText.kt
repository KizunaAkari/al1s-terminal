package com.al1s.terminal.protocol

import java.security.MessageDigest
import org.json.JSONObject

object PackageCanonicalText {
    fun sha256(text:String):String=MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8))
        .joinToString("") {"%02x".format(it)}
    fun verified(task: TaskPackage): String {
        val text = task.canonicalBody ?: CanonicalJson.encode(task.body)
        require(text.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "package_body_too_large" }
        require(CanonicalJson.encode(JSONObject(text)) == CanonicalJson.encode(task.body)) { "package_canonical_body_mismatch" }
        val digest = sha256(text)
        require(digest == task.packageHash) { "package_hash_mismatch" }
        return text
    }
}
