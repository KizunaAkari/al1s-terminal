package com.al1s.terminal.runtime

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors
import org.junit.Assert.*
import org.junit.Test

class ExecutionAdmissionTest {
    @Test fun formalAndDebugAdmissionCannotInterleavePreparationAndStart() {
        val pool=Executors.newFixedThreadPool(2)
        val prepared=CountDownLatch(1);val release=CountDownLatch(1);val formal=CountDownLatch(1)
        try {
            val debug=pool.submit {ExecutionAdmission.withAdmission {prepared.countDown();check(release.await(2,TimeUnit.SECONDS))}}
            assertTrue(prepared.await(2,TimeUnit.SECONDS))
            val next=pool.submit {ExecutionAdmission.withAdmission {formal.countDown()}}
            assertFalse(formal.await(100,TimeUnit.MILLISECONDS))
            release.countDown();debug.get(2,TimeUnit.SECONDS);next.get(2,TimeUnit.SECONDS)
            assertEquals(0L,formal.count)
        } finally {release.countDown();pool.shutdownNow()}
    }
}
