package com.al1s.terminal.runtime

import org.junit.Assert.*
import org.junit.Test

class HelperAttemptProofTest {
    @Test fun startedRecordsRequireAnOwnerAndAcceptedRecordsMustBeUnstarted() {
        assertFalse(HelperAttemptProof.valid("completed",null,"current",null,true))
        assertFalse(HelperAttemptProof.valid("accepted","old","current",null,true))
        assertFalse(HelperAttemptProof.valid("running","old","current",null,true))
        assertTrue(HelperAttemptProof.valid("running","current","current",null,true))
        assertTrue(HelperAttemptProof.valid("interrupted","old","current","old",true))
        assertFalse(HelperAttemptProof.valid("interrupted","old","current","different",true))
        assertTrue(HelperAttemptProof.valid("accepted",null,"current",null,false))
        assertTrue(HelperAttemptProof.valid("completed",null,"current",null,false))
        assertFalse(HelperAttemptProof.valid("accepted",null,null,null,false))
    }
}
