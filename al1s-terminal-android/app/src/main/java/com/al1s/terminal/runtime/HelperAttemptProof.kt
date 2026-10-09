package com.al1s.terminal.runtime

object HelperAttemptProof {
    fun valid(status:String?,owner:String?,runtime:String?,expected:String?,started:Boolean):Boolean {
        if(runtime.isNullOrBlank())return false
        if(started && owner.isNullOrBlank())return false
        if(!expected.isNullOrBlank() && !owner.isNullOrBlank() && expected!=owner)return false
        return when(status) {
            "accepted"->!started && owner==null
            "running"->started && owner==runtime
            "completed"->!started && owner==null || started && !owner.isNullOrBlank()
            "interrupted"->started && !owner.isNullOrBlank()
            else->false
        }
    }
}
