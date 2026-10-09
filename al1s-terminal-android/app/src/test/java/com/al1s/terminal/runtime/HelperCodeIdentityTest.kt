package com.al1s.terminal.runtime

import com.al1s.terminal.broker.HelperCodeIdentity
import org.junit.Assert.*
import org.junit.Test

class HelperCodeIdentityTest {
    @Test fun overwrittenPackageRequiresNewHelperEvenWhenItsBinderIsAlive() {
        assertTrue(HelperCodeIdentity.matches("/data/app/new/base.apk",20,"/data/app/new/base.apk",20))
        assertFalse(HelperCodeIdentity.matches("/data/app/new/base.apk",20,"/data/app/old/base.apk",10))
        assertFalse(HelperCodeIdentity.matches("/data/app/same/base.apk",20,"/data/app/same/base.apk",10))
        assertFalse(HelperCodeIdentity.matches("/data/app/new/base.apk",20,null,0))
    }
    @Test fun existingBinderIsNotAReceiptForANewActivationChallenge() {
        assertFalse(HelperCodeIdentity.activationReceived("new","old",true))
        assertFalse(HelperCodeIdentity.activationReceived("new",null,true))
        assertTrue(HelperCodeIdentity.activationReceived("new","new",true))
        assertFalse(HelperCodeIdentity.activationReceived("new","new",false))
    }
}
