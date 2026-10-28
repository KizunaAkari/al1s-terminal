package com.al1s.terminal.broker

object HelperCodeIdentity {
    fun matches(expectedPath:String,expectedTime:Long,loadedPath:String?,loadedTime:Long):Boolean =
        expectedPath.startsWith("/data/app/") && expectedTime>0 && expectedPath==loadedPath && expectedTime==loadedTime
    fun activationReceived(requested:String,received:String?,alive:Boolean):Boolean = alive && requested==received
}
