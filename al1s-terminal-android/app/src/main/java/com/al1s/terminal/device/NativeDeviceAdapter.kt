package com.al1s.terminal.device

import android.content.Context
import android.graphics.BitmapFactory
import android.os.Process
import android.os.SystemClock
import android.view.InputEvent
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent

/** Runs only in our authenticated shell/root helper, never in the APK UID. */
@Suppress("unused")
class NativeDeviceAdapter(private val context: Context, private val expectedWidth: Int, private val expectedHeight: Int) {
    private var touchStarted = 0L
    private val keys = mutableMapOf<Int, Long>()
    private val injector by lazy {
        val type = Class.forName("android.hardware.input.InputManager")
        val instance = context.getSystemService(Context.INPUT_SERVICE)
        instance to type.getMethod("injectInputEvent", InputEvent::class.java, Int::class.javaPrimitiveType)
    }

    init { require(Process.myUid() in setOf(0, 2000)) }

    private fun checkGeometry() {
        val display = checkNotNull(context.getSystemService(android.hardware.display.DisplayManager::class.java).getDisplay(0))
        val point = android.graphics.Point()
        @Suppress("DEPRECATION") display.getRealSize(point)
        check(point.x == expectedWidth && point.y == expectedHeight) { "screen_geometry_changed" }
    }

    fun capture(): NativePixelFrame {
        val png = DeviceCommand.run(listOf("/system/bin/screencap", "-p"), 32 * 1024 * 1024)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(png, 0, png.size, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0 &&
            bounds.outWidth.toLong() * bounds.outHeight * 4 <= 32 * 1024 * 1024)
        val bitmap = checkNotNull(BitmapFactory.decodeByteArray(png, 0, png.size))
        try {
            val pixels = IntArray(bitmap.width * bitmap.height)
            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
            return NativePixelFrame(bitmap.width, bitmap.height, NativeFramePixels.bgr(pixels, bitmap.width, bitmap.height))
        } finally { bitmap.recycle() }
    }

    @Synchronized fun dispatch(method: Int, x: Int, y: Int, value: String?, forceStop: Boolean): Int =
        try {
            if (method in 6..10 && !(method in 9..10 && x == KeyEvent.KEYCODE_WAKEUP)) {
                val keyguard = context.getSystemService(android.app.KeyguardManager::class.java)
                check(!keyguard.isDeviceSecure || !keyguard.isKeyguardLocked) { "secure_keyguard_locked" }
            }
            if (method in 6..8) checkGeometry()
            if (method in 6..8) require(x in 0 until expectedWidth && y in 0 until expectedHeight)
            when (method) {
                1 -> startApplication(requirePackage(value), forceStop)
                2 -> DeviceCommand.run(listOf("/system/bin/am", "force-stop", requirePackage(value)))
                4 -> DeviceCommand.run(listOf("/system/bin/input", "text", requireNotNull(value)))
                6, 7, 8 -> touch(method, x, y)
                9, 10 -> key(method, x)
                else -> error("unsupported_native_input")
            }
            0
        } catch (_: Exception) { -1 }

    private fun requirePackage(value: String?): String = requireNotNull(value).also {
        require(it.matches(Regex("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+")))
    }

    private fun startApplication(packageName: String, forceStop: Boolean) {
        if (forceStop) DeviceCommand.run(listOf("/system/bin/am", "force-stop", packageName))
        val intent = checkNotNull(context.packageManager.getLaunchIntentForPackage(packageName))
        val component = checkNotNull(intent.component).flattenToString()
        DeviceCommand.run(listOf("/system/bin/am", "start", "-W", "-n", component))
    }

    private fun inject(event: InputEvent) {
        val (instance, method) = injector
        check(method.invoke(instance, event, 2) == true) { "input_injection_rejected" }
    }

    private fun touch(method: Int, x: Int, y: Int) {
        val now = SystemClock.uptimeMillis()
        if (method == 6) { check(touchStarted == 0L); touchStarted = now }
        else check(touchStarted != 0L)
        val action = when (method) { 6 -> MotionEvent.ACTION_DOWN; 7 -> MotionEvent.ACTION_MOVE; else -> MotionEvent.ACTION_UP }
        val event = MotionEvent.obtain(touchStarted, now, action, x.toFloat(), y.toFloat(), 0)
        event.source = InputDevice.SOURCE_TOUCHSCREEN
        try { inject(event) } finally {
            event.recycle()
            if (method == 8) touchStarted = 0
        }
    }

    private fun key(method: Int, code: Int) {
        require(code in 0..KeyEvent.getMaxKeyCode())
        val now = SystemClock.uptimeMillis()
        val started = if (method == 9) now.also { keys[code] = it } else checkNotNull(keys.remove(code))
        inject(KeyEvent(started, now, if (method == 9) KeyEvent.ACTION_DOWN else KeyEvent.ACTION_UP,
            code, 0, 0, -1, 0, 0, InputDevice.SOURCE_KEYBOARD))
    }
}

class NativePixelFrame(val width: Int, val height: Int, val bgr: ByteArray)
