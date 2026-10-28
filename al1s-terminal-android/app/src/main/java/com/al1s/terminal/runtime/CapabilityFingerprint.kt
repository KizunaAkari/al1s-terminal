package com.al1s.terminal.runtime

import org.json.JSONObject
import com.al1s.terminal.protocol.PackageCanonicalText

object CapabilityFingerprint {
    fun of(manifest:JSONObject):String {
        val details=manifest.optJSONObject("details") ?: JSONObject()
        val stable=JSONObject().put("provider_keys",manifest.optJSONArray("provider_keys") ?: org.json.JSONArray())
        for(key in listOf("provider","control_ready","native_ready","maa_version","root_authorized","readiness_error"))
            stable.put(key,details.opt(key) ?: JSONObject.NULL)
        return PackageCanonicalText.sha256(com.al1s.terminal.protocol.CanonicalJson.encode(stable))
    }
}
