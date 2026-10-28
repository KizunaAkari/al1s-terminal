package com.al1s.terminal.runtime

import org.json.JSONArray
import org.json.JSONObject

object ArtifactReferences {
    /** Only verified uploads become references in the accepted formal result. */
    fun confirmed(detail: JSONObject, artifacts: JSONArray, mapping: Map<String,String>): JSONObject {
        val copy = resolve(detail,mapping)
        val references = JSONArray()
        for (index in 0 until artifacts.length()) {
            val artifact=artifacts.getJSONObject(index)
            val local=artifact.getString("artifact_id")
            val remote=mapping[local] ?: continue
            val recording=artifact.getString("kind")=="recording"
            references.put(JSONObject().put("artifact_id",remote).put("local_artifact_id",local)
                .put("artifact_kind",if(recording)"video" else "screenshot")
                .put("file_name",local+if(recording)".mp4" else ".png")
                .put("sha256",artifact.getString("sha256")).put("size_bytes",artifact.getLong("size"))
                .put("mime",artifact.getString("media_type")))
        }
        copy.put("artifacts",references)
        return copy
    }

    fun resolve(detail: JSONObject, mapping: Map<String,String>): JSONObject {
        val copy = JSONObject(detail.toString())
        fun visit(value: Any?, depth: Int) {
            require(depth < 50)
            when (value) {
                is JSONObject -> {
                    val local = value.optString("artifact_id")
                    mapping[local]?.let { remote ->
                        value.put("artifact_id",remote).put("local_artifact_id",local)
                        value.remove("path")
                    }
                    value.keys().asSequence().toList().forEach { visit(value.get(it),depth+1) }
                }
                is JSONArray -> for (index in 0 until value.length()) visit(value.get(index),depth+1)
            }
        }
        visit(copy,0)
        return copy
    }
}
