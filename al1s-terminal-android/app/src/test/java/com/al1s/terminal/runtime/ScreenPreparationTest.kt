package com.al1s.terminal.runtime

import com.al1s.terminal.device.ScreenPreparation
import com.al1s.terminal.device.ScreenState
import com.al1s.terminal.device.ScreenPreparationPort
import org.junit.Assert.*
import org.junit.Test

class ScreenPreparationTest {
    class Phone(var state:ScreenState):ScreenPreparationPort {
        var wakes=0;var dismisses=0;var time=0L;var allowed=true
        override fun state()=state
        override fun nowMs()=time
        override fun pause(){time+=100}
        override fun wake(timeoutMs:Long){check(allowed);wakes++;state=state.copy(interactive=true)}
        override fun dismiss(timeoutMs:Long){check(allowed);dismisses++;state=state.copy(locked=false)}
    }
    @Test fun wakesOnlySleepingPhoneAndDismissesNonsecureLockWithoutHome() {
        val phone=Phone(ScreenState(false,true,false,true))
        ScreenPreparation.prepare(phone)
        assertEquals(1,phone.wakes);assertEquals(1,phone.dismisses)
        ScreenPreparation.prepare(phone)
        assertEquals(1,phone.wakes);assertEquals(1,phone.dismisses)
    }
    @Test fun secureLockOrUnavailableUserStorageRejectsBeforeInput() {
        for(state in listOf(ScreenState(false,true,true,true),ScreenState(false,true,false,false))) {
            val phone=Phone(state)
            try {ScreenPreparation.prepare(phone);fail("must reject unsafe preparation")}
            catch(expected:IllegalStateException){}
            assertEquals(0,phone.wakes);assertEquals(0,phone.dismisses)
        }
    }
    @Test fun canceledAuthorityNeverDispatchesWake() {
        val phone=Phone(ScreenState(false,true,false,true));phone.allowed=false
        try {ScreenPreparation.prepare(phone);fail("must reject revoked input")}
        catch(expected:IllegalStateException){}
        assertEquals(0,phone.wakes)
    }
    @Test fun unreadyDisplayStopsWithinBudget() {
        val phone=object:ScreenPreparationPort {
            var time=0L
            override fun state()=ScreenState(false,false,false,true)
            override fun nowMs()=time
            override fun pause(){time+=100}
            override fun wake(timeoutMs:Long){}
            override fun dismiss(timeoutMs:Long)=error("must not dismiss")
        }
        try {ScreenPreparation.prepare(phone);fail("must time out")}
        catch(expected:IllegalStateException){assertEquals("screen_prepare_timeout",expected.message)}
        assertTrue(phone.time<=8000)
    }
    @Test fun lateWakeCannotActivateAnExpiredPreparation() {
        val phone=object:ScreenPreparationPort {
            var time=0L;var ready=false
            override fun state()=ScreenState(ready,false,false,true)
            override fun nowMs()=time
            override fun pause(){time+=100}
            override fun wake(timeoutMs:Long){time=8001;ready=true}
            override fun dismiss(timeoutMs:Long)=error("must not dismiss")
        }
        try {ScreenPreparation.prepare(phone);fail("late success must not activate")}
        catch(expected:IllegalStateException){assertEquals("screen_prepare_timeout",expected.message)}
    }
}
