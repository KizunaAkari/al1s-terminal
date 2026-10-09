package com.al1s.terminal.runtime

import org.json.JSONObject
import org.json.JSONArray
import org.junit.Assert.*
import org.junit.Test

class ArtifactReferencesTest {
    @Test fun successfulCaptureRemainsDiscoverableInFormalResult() {
        val source=JSONObject()
        val artifacts=JSONArray("""[{"artifact_id":"local","kind":"screenshot","sha256":"abc","size":42,"media_type":"image/png"},
            {"artifact_id":"video","kind":"recording","sha256":"def","size":84,"media_type":"video/mp4"},
            {"artifact_id":"pending","kind":"screenshot","sha256":"ghi","size":12,"media_type":"image/png"}]""")
        val result=ArtifactReferences.confirmed(source,artifacts,mapOf("local" to "remote","video" to "movie"))
        val captures=result.getJSONArray("artifacts")
        assertEquals(2,captures.length())
        assertEquals("screenshot",captures.getJSONObject(0).getString("artifact_kind"))
        assertEquals("remote",captures.getJSONObject(0).getString("artifact_id"))
        assertEquals("local.png",captures.getJSONObject(0).getString("file_name"))
        assertEquals("abc",captures.getJSONObject(0).getString("sha256"))
        assertEquals(42,captures.getJSONObject(0).getLong("size_bytes").toInt())
        assertEquals("image/png",captures.getJSONObject(0).getString("mime"))
        assertEquals("video",captures.getJSONObject(1).getString("artifact_kind"))
        assertFalse(source.has("artifacts"))
    }
    @Test fun confirmedMetadataPreservesLegacyFailureScreenshotReference() {
        val detail=JSONObject("""{"modules":[{"result":{"failure_screenshot":{"artifact_id":"old","path":"private","width":1080}}}]}""")
        val artifacts=JSONArray("""[{"artifact_id":"old","kind":"screenshot","sha256":"abc","size":42,"media_type":"image/png"}]""")
        val result=ArtifactReferences.confirmed(detail,artifacts,mapOf("old" to "remote"))
        val failure=result.getJSONArray("modules").getJSONObject(0).getJSONObject("result").getJSONObject("failure_screenshot")
        assertEquals("remote",failure.getString("artifact_id"))
        assertFalse(failure.has("path"))
        assertEquals(1080,failure.getInt("width"))
        assertEquals("remote",result.getJSONArray("artifacts").getJSONObject(0).getString("artifact_id"))
    }
    @Test fun failureScreenshotUsesConfirmedPlatformArtifactAndHidesLocalPath() {
        val input=JSONObject("""{"modules":[{"result":{"failure_screenshot":{"artifact_id":"local","path":"/data/local/tmp/own.png","width":2400}}}]}""")
        val resolved=ArtifactReferences.resolve(input,mapOf("local" to "remote"))
            .getJSONArray("modules").getJSONObject(0).getJSONObject("result").getJSONObject("failure_screenshot")
        assertEquals("remote",resolved.getString("artifact_id"))
        assertFalse(resolved.has("path"))
        assertEquals(2400,resolved.getInt("width"))
        assertEquals("local",input.getJSONArray("modules").getJSONObject(0).getJSONObject("result")
            .getJSONObject("failure_screenshot").getString("artifact_id"))
    }
}
