package com.al1s.terminal.runtime

import com.al1s.terminal.activation.LocalShellCompletion
import org.junit.Assert.*
import org.junit.Test

class LocalShellCompletionTest {
    @Test fun aClosedBackgroundCommandCanAdvanceToIndependentBinderVerification() {
        var elapsed = 0L
        var closed = false
        LocalShellCompletion.awaitClosed({ closed }, { true }, { elapsed }) { elapsed += 25_000_000; closed = true }
        assertTrue(closed)
    }

    @Test fun aStalledCommandAndUserCancellationCannotWaitForever() {
        var elapsed = 0L
        assertThrows(IllegalStateException::class.java) {
            LocalShellCompletion.awaitClosed({ false }, { true }, { elapsed }) { elapsed += 1_000_000_000 }
        }
        assertThrows(IllegalStateException::class.java) {
            LocalShellCompletion.awaitClosed({ false }, { false }, { 0 }) { fail("Cancelled command waited") }
        }
    }
}
