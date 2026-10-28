package com.al1s.terminal.runtime

import org.junit.Assert.*
import org.junit.Test

class AttemptReconciliationTest {
    @Test fun `accepted proof must belong to the current helper and contain no prior start`() {
        assertEquals(AttemptReconciliation.Action.WAIT, AttemptReconciliation.decide("accepted", true, false, true,
            epochMatches=false, started=true))
        assertEquals(AttemptReconciliation.Action.WAIT, AttemptReconciliation.decide("accepted", true, false, true,
            epochMatches=true, started=true))
        assertEquals(AttemptReconciliation.Action.START, AttemptReconciliation.decide("accepted", true, false, true,
            epochMatches=true, started=false))
        assertEquals(AttemptReconciliation.Action.WAIT, AttemptReconciliation.decide("running", true, false, true,
            epochMatches=false, started=true))
    }
    @Test fun `surviving execution is observed and never restarted`() {
        assertEquals(AttemptReconciliation.Action.OBSERVE, AttemptReconciliation.decide("running", true, false, true))
        assertEquals(AttemptReconciliation.Action.CANCEL, AttemptReconciliation.decide("running", true, true, true))
    }
    @Test fun `unknown or mismatched helper state blocks replay`() {
        assertEquals(AttemptReconciliation.Action.WAIT, AttemptReconciliation.decide(null, true, false, true))
        assertEquals(AttemptReconciliation.Action.WAIT, AttemptReconciliation.decide("absent", true, false, true))
        assertEquals(AttemptReconciliation.Action.WAIT, AttemptReconciliation.decide("completed", false, false, true))
    }
    @Test fun `accepted proof permits the same unstarted attempt but final state only reports`() {
        assertEquals(AttemptReconciliation.Action.START, AttemptReconciliation.decide("accepted", true, false, true))
        assertEquals(AttemptReconciliation.Action.SETTLE_UNSTARTED, AttemptReconciliation.decide("accepted", true, false, false))
        assertEquals(AttemptReconciliation.Action.REPORT, AttemptReconciliation.decide("completed", true, false, false))
        assertEquals(AttemptReconciliation.Action.REPORT, AttemptReconciliation.decide("interrupted", true, false, false))
    }
}
