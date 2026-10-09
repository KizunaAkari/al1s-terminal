package com.al1s.terminal.activation

import android.content.Context
import android.os.Build
import io.github.muntashirakon.adb.AbsAdbConnectionManager
import java.security.PrivateKey
import java.security.cert.Certificate
import java.util.concurrent.TimeUnit

class LocalAdbConnector(context: Context) : AbsAdbConnectionManager() {
    private val identity = PairingIdentityStore(context).loadOrCreate()
    init {
        setApi(Build.VERSION.SDK_INT)
        setHostAddress("127.0.0.1")
        setTimeout(15, TimeUnit.SECONDS)
        setThrowOnUnauthorised(true)
    }
    override fun getPrivateKey(): PrivateKey = identity.first
    override fun getCertificate(): Certificate = identity.second
    override fun getDeviceName(): String = "AL1S Local Device"

    fun pairLocal(port: Int, code: String): Boolean {
        require(LocalAdbEndpoint.accepts("127.0.0.1", port))
        require(code.matches(Regex("[0-9]{6}"))) { "请输入六位配对码" }
        return pair("127.0.0.1", port, code)
    }
    fun connectLocal(port: Int): Boolean {
        require(LocalAdbEndpoint.accepts("127.0.0.1", port))
        return connect("127.0.0.1", port)
    }
}
