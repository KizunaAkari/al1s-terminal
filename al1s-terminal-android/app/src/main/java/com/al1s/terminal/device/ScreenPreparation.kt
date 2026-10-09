package com.al1s.terminal.device

data class ScreenState(val interactive:Boolean,val locked:Boolean,val secure:Boolean,val userUnlocked:Boolean)

interface ScreenPreparationPort {
    fun state():ScreenState
    fun wake(timeoutMs:Long)
    fun dismiss(timeoutMs:Long)
    fun nowMs():Long
    fun pause()
}

object ScreenPreparation {
    fun prepare(port:ScreenPreparationPort) {
        val deadline=port.nowMs()+8000
        fun remaining():Long = (deadline-port.nowMs()).also {check(it>0) {"screen_prepare_timeout"}}
        fun state():ScreenState = port.state().also {
            check(it.userUnlocked) {"user_storage_locked"}
            check(!it.secure) {"secure_keyguard"}
        }
        if(!state().interactive)port.wake(remaining())
        var dismissed=false
        while(true) {
            val current=state()
            val budget=remaining()
            if(current.interactive && !current.locked)return
            if(current.interactive && current.locked && !dismissed) {
                port.dismiss(budget);dismissed=true
            }
            port.pause()
        }
    }
}
