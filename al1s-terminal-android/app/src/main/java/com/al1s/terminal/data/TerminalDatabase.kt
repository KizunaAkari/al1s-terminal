package com.al1s.terminal.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [InboxTaskEntity::class, OutboxReportEntity::class, ProcessedCommandEntity::class,
        PackageResourceEntity::class, MaaAttemptLinkEntity::class, SetupCheckReportEntity::class,
        QuickTestInboxEntity::class,QuickTestResourceEntity::class,QuickTestEventEntity::class],
    version = 4,
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
                ).addMigrations(MaaRoomMigration.FROM_1_TO_2,MaaRoomMigration.FROM_2_TO_3,
                    MaaRoomMigration.FROM_3_TO_4).build().also { instance = it }
            }
    }
}
