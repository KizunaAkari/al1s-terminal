package com.al1s.terminal.broker

import java.security.MessageDigest

class BrokerAuthority(private val appUid: Int, val epoch: String, private val secret: String) {
    fun accepts(callerUid: Int, expectedEpoch: String, suppliedSecret: String, deadline: Long, now: Long): Boolean =
        callerUid == appUid && expectedEpoch == epoch && deadline > now && deadline - now <= 1800 &&
            MessageDigest.isEqual(secret.toByteArray(), suppliedSecret.toByteArray())
}
