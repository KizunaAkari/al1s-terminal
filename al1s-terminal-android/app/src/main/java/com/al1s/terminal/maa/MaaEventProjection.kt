package com.al1s.terminal.maa

import org.json.JSONObject

/** Native callbacks are copied into bounded facts; the task owner handles delivery. */
class MaaEventProjection {
    private val events = ArrayDeque<JSONObject>()
    private var sequence = 0L
    var truncated: Boolean = false
        private set
    @Volatile var observer: ((String, JSONObject) -> Unit)? = null
    private var retainedCharacters=0

    @Synchronized fun onEvent(message: String, details: String) {
        if (details.length > 262144) { truncated = true; return }
        val body = runCatching { JSONObject(details) }.getOrDefault(JSONObject())
        observer?.invoke(message, body)
        val retained = if(details.length<=32768) body else JSONObject().put("name",body.optString("name"))
            .put("truncated",true).also {truncated=true}
        val event = JSONObject().put("sequence", ++sequence).put("message", message.take(160)).put("details", retained)
        val length=event.toString().length
        while(events.isNotEmpty() && (events.size>=512 || retainedCharacters+length>524288)) {
            retainedCharacters-=events.removeFirst().toString().length;truncated=true
        }
        events.addLast(event)
        retainedCharacters+=length
    }

    @Synchronized fun snapshot(): String = JSONObject().put("events", org.json.JSONArray(events.toList()))
        .put("truncated", truncated).toString()
}
