package com.al1s.terminal.broker

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.Binder
import android.os.Bundle

class BrokerBootstrapProvider : ContentProvider() {
    override fun onCreate(): Boolean = true
    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        android.util.Log.i("AL1S-Bootstrap", "call_uid:${Binder.getCallingUid()}")
        check(Binder.getCallingUid() in setOf(0, 2000)) { "Bootstrap caller rejected" }
        check(method == "handoff" && extras != null) { "Bootstrap method rejected" }
        val accepted = BrokerClient.handoff(arg, extras)
        android.util.Log.i("AL1S-Bootstrap", "handoff_accepted:$accepted")
        return Bundle().apply { putBoolean("accepted", accepted) }
    }
    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0
}
