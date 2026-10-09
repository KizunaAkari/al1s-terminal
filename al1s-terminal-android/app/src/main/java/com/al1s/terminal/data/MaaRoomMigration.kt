package com.al1s.terminal.data

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

object MaaRoomMigration {
    val FROM_3_TO_4 = object:Migration(3,4) {
        override fun migrate(db:SupportSQLiteDatabase) {
            db.execSQL("CREATE TABLE IF NOT EXISTS quick_test_inbox (sessionId TEXT NOT NULL, scriptId TEXT NOT NULL, candidateVersionId TEXT NOT NULL, candidateManifestHash TEXT NOT NULL, definitionHash TEXT NOT NULL, terminalId TEXT NOT NULL, deviceId TEXT NOT NULL, packageHash TEXT NOT NULL, state TEXT NOT NULL, expiresAt INTEGER NOT NULL, reportId TEXT NOT NULL, entryDefinitionKey TEXT NOT NULL, debugStepNumber INTEGER, lastHelperSequence INTEGER NOT NULL, lastEventSequence INTEGER NOT NULL, resultJson TEXT, createdAt INTEGER NOT NULL, updatedAt INTEGER NOT NULL, PRIMARY KEY(sessionId))")
            db.execSQL("CREATE INDEX IF NOT EXISTS index_quick_test_inbox_terminalId_state_createdAt ON quick_test_inbox(terminalId,state,createdAt)")
            db.execSQL("CREATE TABLE IF NOT EXISTS quick_test_resources (sessionId TEXT NOT NULL, resourceKey TEXT NOT NULL, blobId TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mediaType TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(sessionId,resourceKey), FOREIGN KEY(sessionId) REFERENCES quick_test_inbox(sessionId) ON UPDATE NO ACTION ON DELETE RESTRICT)")
            db.execSQL("CREATE TABLE IF NOT EXISTS quick_test_events (sessionId TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL, stepNumber INTEGER, code TEXT, createdAt TEXT NOT NULL, confirmed INTEGER NOT NULL, PRIMARY KEY(sessionId,sequence), FOREIGN KEY(sessionId) REFERENCES quick_test_inbox(sessionId) ON UPDATE NO ACTION ON DELETE RESTRICT)")
            db.execSQL("CREATE INDEX IF NOT EXISTS index_quick_test_events_sessionId_confirmed_sequence ON quick_test_events(sessionId,confirmed,sequence)")
        }
    }
    val FROM_2_TO_3 = object : Migration(2,3) {
        override fun migrate(db:SupportSQLiteDatabase) {
            db.execSQL("CREATE TABLE IF NOT EXISTS setup_check_reports (requestId TEXT NOT NULL, terminalId TEXT NOT NULL, instanceId TEXT NOT NULL, payloadJson TEXT NOT NULL, confirmed INTEGER NOT NULL, createdAt INTEGER NOT NULL, PRIMARY KEY(requestId))")
            db.execSQL("CREATE INDEX IF NOT EXISTS index_setup_check_reports_terminalId_confirmed_createdAt ON setup_check_reports(terminalId,confirmed,createdAt)")
        }
    }
    val FROM_1_TO_2 = object : Migration(1, 2) {
        override fun migrate(db: SupportSQLiteDatabase) {
            db.execSQL("CREATE TABLE IF NOT EXISTS package_resources (packageId TEXT NOT NULL, resourceKey TEXT NOT NULL, blobId TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mediaType TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(packageId,resourceKey), FOREIGN KEY(packageId) REFERENCES inbox_tasks(packageId) ON UPDATE NO ACTION ON DELETE RESTRICT)")
            db.execSQL("CREATE INDEX IF NOT EXISTS index_package_resources_sha256 ON package_resources(sha256)")
            db.execSQL("CREATE TABLE IF NOT EXISTS maa_attempt_links (attemptId TEXT NOT NULL, packageId TEXT NOT NULL, helperEpoch TEXT, helperStatus TEXT NOT NULL, lastEventSequence INTEGER NOT NULL, updatedAt INTEGER NOT NULL, PRIMARY KEY(attemptId), FOREIGN KEY(packageId) REFERENCES inbox_tasks(packageId) ON UPDATE NO ACTION ON DELETE RESTRICT)")
            db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_maa_attempt_links_packageId ON maa_attempt_links(packageId)")
            db.execSQL("CREATE INDEX IF NOT EXISTS index_inbox_tasks_attemptId ON inbox_tasks(attemptId)")
        }
    }
}
