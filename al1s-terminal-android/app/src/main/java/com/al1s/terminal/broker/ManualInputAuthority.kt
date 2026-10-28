package com.al1s.terminal.broker

/** Formal offline permits remain separate. This short authority covers human input only. */
class ManualInputAuthority(private val epoch:String,
    private val now:()->Long=android.os.SystemClock::elapsedRealtime,
    private val wallNow:()->Long=System::currentTimeMillis) {
    private var enforced=false
    private var generation=0L
    private var deadline=0L
    private var granted=false
    @Synchronized fun bind() {enforced=true}
    @Synchronized fun update(instance:String,version:Long,allowed:Boolean,until:Long) {
        require(instance==epoch && version>=generation && version>0) {"input_authority_generation_changed"}
        val remaining=until-wallNow()
        require(remaining in 0..30000) {"input_authority_deadline_invalid"}
        generation=version;granted=allowed;deadline=now()+remaining
    }
    @Synchronized fun revoke() {granted=false;deadline=0}
    @Synchronized fun allowsInput() = !enforced || granted && now()<deadline
    @Synchronized fun requireInput() {check(!enforced || granted && now()<deadline) {"input_authority_unavailable"}}
}
