package com.al1s.terminal

import android.app.Application
import com.al1s.terminal.data.TerminalDatabase
import com.al1s.terminal.runtime.SyncEngine
import com.al1s.terminal.security.IdentityStore

class TerminalApplication : Application() {
    val identityStore: IdentityStore by lazy { IdentityStore(this) }
    val database: TerminalDatabase by lazy { TerminalDatabase.open(this) }

    fun syncEngine(): SyncEngine = SyncEngine(this, identityStore, database)
}
