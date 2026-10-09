package com.al1s.terminal.broker

import android.content.Context
import android.os.Process

/** Only the separately launched privileged process constructs this context. */
// These compatibility calls run only in UID 0/2000 app_process, never in the normal
// target-SDK application. Each OS must still pass the real activation probe.
@android.annotation.SuppressLint("BlockedPrivateApi", "SoonBlockedPrivateApi")
object BrokerProcessContext {
    fun create(): Context {
        check(Process.myUid() in setOf(0, 2000))
        val threadType = Class.forName("android.app.ActivityThread")
        // Initialize a shell-side runtime, not ActivityThread.attach(system=true), which
        // gives this independent process a system-process identity.
        val thread = threadType.getDeclaredConstructor().apply { isAccessible = true }.newInstance()
        threadType.getDeclaredField("sCurrentActivityThread").apply { isAccessible = true }.set(null, thread)
        threadType.getDeclaredField("mSystemThread").apply { isAccessible = true }.setBoolean(thread, true)
        if (android.os.Build.VERSION.SDK_INT >= 31) {
            val configuration = Class.forName("android.app.ConfigurationController")
                .getDeclaredConstructor(Class.forName("android.app.ActivityThreadInternal"))
                .apply { isAccessible = true }.newInstance(thread)
            threadType.getDeclaredField("mConfigurationController").apply { isAccessible = true }.set(thread, configuration)
        }
        val system = threadType.getDeclaredMethod("getSystemContext").invoke(thread) as Context
        val shell = system.createPackageContext("com.android.shell", 0)
        val implementation = Class.forName("android.app.ContextImpl")
        val loaded = implementation.getDeclaredField("mPackageInfo").apply { isAccessible = true }.get(shell)
        // createPackageContext inherits the parent's op-package ("android"). A fresh app
        // context has shell's own attribution, which Android 15 checks against UID 2000.
        val factory = implementation.getDeclaredMethod("createAppContext", threadType, Class.forName("android.app.LoadedApk"))
            .apply { isAccessible = true }
        return (factory.invoke(null, thread, loaded) as Context).also {
            check(it.packageName == "com.android.shell")
            if (android.os.Build.VERSION.SDK_INT >= 29) check(it.opPackageName == "com.android.shell")
        }
    }
}
