package com.al1s.terminal.activation

/** A pairing code is consumed once and is deliberately never stored in this gate. */
class PairingRequestGate(val sessionId: String, private val openedAtSeconds: Long) {
    private var cancelled = false
    private var consumed = false

    @Synchronized fun accept(session: String?, code: String, nowSeconds: Long): Boolean {
        if (cancelled || consumed || session != sessionId || !code.matches(Regex("[0-9]{6}")) ||
            nowSeconds < openedAtSeconds || nowSeconds >= openedAtSeconds + 180) return false
        consumed = true
        return true
    }

    @Synchronized fun cancel() { cancelled = true }
    @Synchronized fun isActive(nowSeconds: Long) = !cancelled && nowSeconds in openedAtSeconds until openedAtSeconds + 180
    @Synchronized fun awaitingCode(nowSeconds: Long) = !consumed && isActive(nowSeconds)
}
