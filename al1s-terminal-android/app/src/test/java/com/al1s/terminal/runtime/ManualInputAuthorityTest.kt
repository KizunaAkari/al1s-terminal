package com.al1s.terminal.runtime

import com.al1s.terminal.broker.ManualInputAuthority
import org.junit.Test
import org.junit.Assert.*

class ManualInputAuthorityTest {
    @Test(expected=IllegalStateException::class) fun expiredAuthorityRejectsDelayedInput() {
        var now=1000L;val authority=ManualInputAuthority("epoch",now={now},wallNow={now})
        authority.bind();authority.update("epoch",1,true,2000);authority.requireInput()
        now=2000;authority.requireInput()
    }
    @Test(expected=IllegalArgumentException::class) fun oldGenerationCannotRegrantInput() {
        val authority=ManualInputAuthority("epoch",now={1000L},wallNow={1000L})
        authority.bind();authority.update("epoch",2,false,2000);authority.update("epoch",1,true,2000)
    }
    @Test fun deepSleepExpiryRequiresANewGrantAndRevocationWins() {
        var elapsed=1000L;var wall=1000L
        val authority=ManualInputAuthority("epoch",now={elapsed},wallNow={wall})
        authority.bind();authority.update("epoch",1,true,26000)
        elapsed+=30000;wall+=30000
        assertFalse(authority.allowsInput())
        authority.update("epoch",2,true,wall+25000);assertTrue(authority.allowsInput())
        authority.revoke();assertFalse(authority.allowsInput())
    }
}
