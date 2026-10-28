package com.al1s.terminal.activation

object LocalAdbEndpoint {
    fun accepts(host: String, port: Int): Boolean = host in setOf("127.0.0.1", "::1") && port in 1..65535
}

object WirelessRecoveryPolicy {
    fun shouldEnable(consented: Boolean, permissionGranted: Boolean, paired: Boolean): Boolean =
        consented && permissionGranted && paired
}
