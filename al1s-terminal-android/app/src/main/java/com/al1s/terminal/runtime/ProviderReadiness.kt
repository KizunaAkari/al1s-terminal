package com.al1s.terminal.runtime

object ProviderReadiness {
    fun keys(version:String?,nativeReady:Boolean,ocrReady:Boolean):List<String> {
        if(version?.removePrefix("v")!="5.12.1" || !nativeReady)return emptyList()
        val keys=mutableListOf("maa","editor-session-v1")
        if(ocrReady)keys+="ocr"
        return keys
    }
}
