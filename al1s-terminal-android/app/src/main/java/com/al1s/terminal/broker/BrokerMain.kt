package com.al1s.terminal.broker

import android.content.Context
import android.net.Uri
import android.os.Binder
import android.os.Bundle
import android.os.IBinder
import android.os.Looper
import android.os.Parcel
import android.os.Process
import android.util.Base64
import java.security.SecureRandom
import java.util.UUID

@androidx.annotation.RequiresApi(27)
object BrokerMain {
    private const val OWNER = "com.al1s.terminal"

    @JvmStatic fun main(args: Array<String>) {
        println("AL1S_BOOTSTRAP_ENTER")
        check(android.os.Build.VERSION.SDK_INT >= 27) { "native_provider_requires_api_27" }
        require(Process.myUid() in setOf(0, 2000))
        require(args.size == 1 && args[0].matches(Regex("[A-Za-z0-9_-]{40,80}=?")))
        Looper.prepareMainLooper()
        val context = BrokerProcessContext.create()
        println("AL1S_BOOTSTRAP_CONTEXT_READY")
        val application = context.packageManager.getApplicationInfo(OWNER, 0)
        println("BOOT_PACKAGE")
        val uid = application.uid
        if (HelperRebindServer.rebindExisting(uid, application.sourceDir, args[0])) return
        val epoch = UUID.randomUUID().toString()
        val secret = Base64.encodeToString(ByteArray(36).also { SecureRandom().nextBytes(it) },
            Base64.NO_WRAP or Base64.URL_SAFE)
        println("BOOT_KEYS")
        val workspace = com.al1s.terminal.execution.HelperWorkspace(uid, Process.myUid())
        val operations = BrokerDeviceOperations(application.nativeLibraryDir, context, workspace, epoch)
        val installedAt=context.packageManager.getPackageInfo(OWNER,0).lastUpdateTime
        val channel = DeviceBinder(BrokerAuthority(uid, epoch, secret), operations,application.sourceDir,installedAt)
        println("BOOT_BINDER")
        val payload = Bundle().apply {
            putBinder("binder", channel); putString("epoch", epoch); putString("secret", secret)
        }
        val accepted = BrokerHandoff.deliver(args[0], payload)
        check(accepted) { "Bootstrap was not accepted" }
        println("AL1S_BOOTSTRAP_ACCEPTED")
        HelperRebindServer(uid, application.sourceDir, payload, operations::beginReplacement) {
            operations.close()
            Process.killProcess(Process.myPid())
        }.start()
        Looper.loop()
    }

    @androidx.annotation.RequiresApi(27)
    private class DeviceBinder(private val authority: BrokerAuthority, private val operations: BrokerDeviceOperations,
        private val apk:String,private val installedAt:Long) : Binder() {
        override fun onTransact(code: Int, data: Parcel, reply: Parcel?, flags: Int): Boolean {
            if (code != IBinder.FIRST_CALL_TRANSACTION || reply == null) return super.onTransact(code, data, reply, flags)
            try {
                data.enforceInterface("com.al1s.terminal.device-broker-v1")
                val epoch = data.readString().orEmpty(); val secret = data.readString().orEmpty()
                val deadline = data.readLong()
                check(authority.accepts(Binder.getCallingUid(), epoch, secret, deadline, System.currentTimeMillis() / 1000))
                val operation = data.readString().orEmpty()
                val payload = data.readBundle(BrokerMain::class.java.classLoader) ?: Bundle()
                val result = Bundle(operations.invoke(operation, payload)).apply {
                    putInt("uid", Process.myUid()); putString("epoch", authority.epoch)
                    putString("helper_apk",apk);putLong("helper_apk_updated_at",installedAt)
                }
                @Suppress("DEPRECATION") val memory = result.getParcelable<android.os.SharedMemory>("image")
                @Suppress("DEPRECATION") val video = result.getParcelable<android.os.ParcelFileDescriptor>("video")
                @Suppress("DEPRECATION") val artifact = result.getParcelable<android.os.ParcelFileDescriptor>("artifact")
                try {
                    reply.writeNoException()
                    reply.writeBundle(result)
                } finally { memory?.close(); video?.close(); artifact?.close() }
            } catch (error: Exception) { reply.writeException(error) }
            catch (error: LinkageError) { reply.writeException(IllegalStateException("native_runtime_unavailable:${error.javaClass.simpleName}")) }
            return true
        }
    }
}
