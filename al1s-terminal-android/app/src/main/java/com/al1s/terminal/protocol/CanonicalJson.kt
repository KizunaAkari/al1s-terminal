package com.al1s.terminal.protocol

import org.json.JSONArray
import org.json.JSONObject

object CanonicalJson {
    fun encode(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(
            prefix = "{",
            postfix = "}",
            separator = ",",
        ) { key -> "${JSONObject.quote(key)}:${encode(value.get(key))}" }
        is JSONArray -> (0 until value.length()).joinToString(
            prefix = "[",
            postfix = "]",
            separator = ",",
        ) { index -> encode(value.get(index)) }
        is String -> JSONObject.quote(value)
        is Boolean, is Int, is Long -> value.toString()
        is Number -> JSONObject.numberToString(value)
        else -> error("Unsupported JSON value ${value::class.java.name}")
    }
}
