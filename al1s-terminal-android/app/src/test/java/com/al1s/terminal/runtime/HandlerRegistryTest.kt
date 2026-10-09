package com.al1s.terminal.runtime

import com.al1s.terminal.maa.HandlerRegistry
import org.json.JSONObject
import org.junit.Test

class HandlerRegistryTest {
    @Test fun `native recognition and bounded actions use explicit registry`() {
        HandlerRegistry.requireAll(JSONObject("""{"Entry":{"recognition":"DirectHit","action":"DoNothing","next":[]}}"""))
    }
    @Test(expected = IllegalArgumentException::class)
    fun `missing custom handlers are rejected before any input`() {
        HandlerRegistry.requireAll(JSONObject("""{"Entry":{"recognition":"Custom","custom_recognition":"MissingHandler","action":"Click"}}"""))
    }
    @Test(expected = IllegalArgumentException::class)
    fun `external shell action cannot enter a native pipeline`() {
        HandlerRegistry.requireAll(JSONObject("""{"Entry":{"recognition":"DirectHit","action":"Command","exec":"am start"}}"""))
    }
}
