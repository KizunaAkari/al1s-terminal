package com.al1s.terminal.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [InboxTaskEntity::class, OutboxReportEntity::class, ProcessedCommandEntity::class],
    version = 1,
    exportSchema = true,
)
abstract class TerminalDatabase : RoomDatabase() {
    abstract fun terminalDao(): TerminalDao

    companion object {
        @Volatile
        private var instance: TerminalDatabase? = null

        fun open(context: Context): TerminalDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    TerminalDatabase::class.java,
                    "al1s-terminal.db",
                ).build().also { instance = it }
            }
    }
}
