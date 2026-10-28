package com.al1s.terminal.runtime

import org.junit.Assert.*
import org.junit.Test

class ProviderReadinessTest {
    @Test fun providerKeysRequireActualNativeAndOcrProof() {
        assertEquals(emptyList<String>(),ProviderReadiness.keys(null,false,false))
        assertEquals(emptyList<String>(),ProviderReadiness.keys("v5.12.1",false,true))
        assertEquals(listOf("maa","editor-session-v1"),ProviderReadiness.keys("v5.12.1",true,false))
        assertEquals(listOf("maa","editor-session-v1","ocr"),ProviderReadiness.keys("v5.12.1",true,true))
        assertEquals(emptyList<String>(),ProviderReadiness.keys("v5.12.2",true,true))
    }
}
