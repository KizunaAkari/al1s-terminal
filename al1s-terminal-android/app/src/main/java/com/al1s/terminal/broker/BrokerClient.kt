package com.al1s.terminal.broker

import android.os.Bundle
import android.os.IBinder
import android.os.Parcel
import java.security.SecureRandom
import android.util.Base64

object BrokerClient {
    @Volatile private var binder: IBinder? = null
    @Volatile private var pendingNonce: String? = null
    @Volatile private var epoch: String? = null
    @Volatile private var secret: String? = null
    @Volatile private var acceptedNonce:String?=null

    @Synchronized fun beginActivation(): String = Base64.encodeToString(ByteArray(32).also {
        SecureRandom().nextBytes(it)
    }, Base64.NO_WRAP or Base64.URL_SAFE).also { pendingNonce = it }

    @Synchronized fun handoff(nonce: String?, bundle: Bundle): Boolean {
        if (nonce == null || nonce != pendingNonce) {
            android.util.Log.i("AL1S-Bootstrap", "challenge_matched:false")
            return false
        }
        android.util.Log.i("AL1S-Bootstrap", "challenge_matched:true")
        val channel = bundle.getBinder("binder") ?: return false
        val generation = bundle.getString("epoch") ?: return false
        val key = bundle.getString("secret") ?: return false
        channel.linkToDeath({ clear(channel) }, 0)
        android.util.Log.i("AL1S-Bootstrap", "death_linked:true")
        binder = channel; epoch = generation; secret = key; pendingNonce = null;acceptedNonce=nonce
        return true
    }
    @Synchronized private fun clear(expected: IBinder) {
        if (binder === expected) { binder = null; epoch = null; secret = null;acceptedNonce=null }
    }
    fun connected(): Boolean = binder?.isBinderAlive == true
    fun activationReceived(nonce:String)=HelperCodeIdentity.activationReceived(nonce,acceptedNonce,connected())
    fun codeCurrent(context:android.content.Context):Boolean = if(!connected())false else runCatching {
        val info=context.packageManager.getApplicationInfo(context.packageName,0)
        val installedAt=context.packageManager.getPackageInfo(context.packageName,0).lastUpdateTime
        val state=invoke("observe")
        HelperCodeIdentity.matches(info.sourceDir,installedAt,state.getString("helper_apk"),state.getLong("helper_apk_updated_at"))
    }.getOrDefault(false)

    @Synchronized fun cancelActivation(nonce: String) {
        if (pendingNonce == nonce) pendingNonce = null
    }

    fun invoke(operation: String, payload: Bundle = Bundle()): Bundle {
        val channel = binder ?: error("control_activation_required")
        val data = Parcel.obtain(); val reply = Parcel.obtain()
        try {
            data.writeInterfaceToken("com.al1s.terminal.device-broker-v1")
            data.writeString(epoch); data.writeString(secret)
            data.writeLong(System.currentTimeMillis() / 1000 + 30)
            data.writeString(operation); data.writeBundle(payload)
            check(channel.transact(IBinder.FIRST_CALL_TRANSACTION, data, reply, 0))
            reply.readException()
            return reply.readBundle(BrokerClient::class.java.classLoader) ?: Bundle()
        } finally { data.recycle(); reply.recycle() }
    }
}
