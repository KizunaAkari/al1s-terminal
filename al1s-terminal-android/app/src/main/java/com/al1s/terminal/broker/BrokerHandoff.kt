package com.al1s.terminal.broker

import android.os.Bundle
import android.os.Process

/** Uses the same external-provider acquisition as Android's shell `content` command. */
object BrokerHandoff {
    private const val AUTHORITY = "com.al1s.terminal.broker.bootstrap"

    fun deliver(nonce: String, payload: Bundle): Boolean {
        check(Process.myUid() in setOf(0, 2000))
        return deliverThroughSystemCommand(nonce, payload)
    }

    private fun deliverThroughSystemCommand(nonce: String, payload: Bundle): Boolean {
        val loader = dalvik.system.PathClassLoader("/system/framework/content.jar", BrokerHandoff::class.java.classLoader)
        val type = Class.forName("com.android.commands.content.Content\$CallCommand", true, loader)
        val constructor = type.getDeclaredConstructor(android.net.Uri::class.java, Int::class.javaPrimitiveType,
            String::class.java, String::class.java, Bundle::class.java).apply { isAccessible = true }
        val command = constructor.newInstance(android.net.Uri.parse("content://$AUTHORITY"), 0, "handoff", nonce, payload)
        val execute = type.superclass.getDeclaredMethod("execute").apply { isAccessible = true }
        val original = System.out
        val bytes = java.io.ByteArrayOutputStream()
        try {
            System.setOut(java.io.PrintStream(bytes))
            execute.invoke(command)
        } finally { System.setOut(original) }
        val result = bytes.toString("UTF-8").trim()
        return result.lines().any { it == "Result: Bundle[{accepted=true}]" }
    }

}
